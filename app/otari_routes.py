"""Branch otari: the Otari panel's buttons, the guardrails service Otari calls, and the demo script page.

Every route answers 404 unless LLM_GATEWAY=otari. None returns a key, an environment value or a log line.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import gateway
from .llm import LLMClient, LLMConfig

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_PROBES = 60  # per process: the buttons cost a fraction of a cent each, but the page is public
_probes = {"n": 0}
_lock = threading.Lock()


def _need_active() -> None:
    if not gateway.active():
        raise HTTPException(404, "Not found")


def _take_probe() -> None:
    with _lock:
        if _probes["n"] >= MAX_PROBES:
            raise HTTPException(429, "The demo buttons have been used up for this process.")
        _probes["n"] += 1


# ------------------------------------------------------------ guardrails service (Otari -> app)

class ValidateRequest(BaseModel):
    profile: str
    input_text: str = ""
    validate_kwargs: dict | None = None


@router.post("/otari-guardrails/validate", include_in_schema=False)
def guard_validate(req: ValidateRequest) -> dict:
    _need_active()
    if req.profile != gateway.GUARD_PROFILE:
        raise HTTPException(404, "Unknown profile")
    return {"profile": req.profile, "result": gateway.guard_check(req.input_text)}


@router.get("/otari-guardrails/profiles", include_in_schema=False)
def guard_profiles() -> list[dict]:
    _need_active()
    return [{"name": gateway.GUARD_PROFILE, "guardrail_name": "contact_data_regex"}]


# ------------------------------------------------------------ panel

def _money(v) -> float | None:
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return None


def user_status(user: str) -> dict:
    row = gateway.end_user(user) or {}
    budgets = gateway.end_user_budgets()
    bid = row.get("budget_id") or gateway.USER_BUDGETS[user]
    b = budgets.get(bid) or {}
    limit = _money(b.get("max_budget"))
    spend = _money(row.get("spend"))
    return {"user": user, "budget": bid, "limit_usd": limit, "spent_usd": spend,
            "left_usd": round(limit - (spend or 0), 6) if limit is not None else None,
            "blocked": bool(row.get("blocked")), "known": bool(row)}


@router.get("/api/otari/status")
def otari_status() -> dict:
    _need_active()
    return {**gateway.info(), "users": [user_status(u) for u in gateway.USER_BUDGETS],
            "key_ceiling": gateway.key_budget(),
            "probes_left": MAX_PROBES - _probes["n"]}


class ProbeRequest(BaseModel):
    user: str | None = None


def _tiny_call(policy: str, user: str, text: str, guard: bool = False) -> dict:
    body, headers = gateway.user_fields(user)
    if guard:
        body["guardrails"] = gateway.guard_request()
    client = LLMClient(LLMConfig(base_url=gateway.base_url(), model=policy, api_key=gateway.api_key(), timeout=45,
                                 max_tokens=300, max_retries=0, extra_body=body, extra_headers=headers))
    t0 = time.monotonic()
    out = {"policy": policy, "user": user}
    try:
        client.chat([{"role": "system", "content": 'Answer with the JSON object {"ok": true}.'},
                     {"role": "user", "content": text}])
        h = client.usage.headers
        out.update(ok=True, status=200, model=h.get("otari-model"), fallback=h.get("otari-fallback") == "true",
                   attempts=int(h.get("otari-attempt-count") or 1), cost_usd=client.usage.gateway_cost_usd,
                   guardrails=h.get("otari-guardrails"))
    except RuntimeError as exc:
        msg = str(exc)
        out.update(ok=False, status=int(msg.split()[1]) if msg.startswith("HTTP ") else 502, error=msg[:240])
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


@router.post("/api/otari/probe")
def otari_probe(req: ProbeRequest) -> dict:
    """One web search through Otari billed to the chosen end user ($0.008 on its own budget):
    200 while the user's budget covers it, 403 budget_exceeded once it does not."""
    _need_active()
    _take_probe()
    user = gateway.clean_user(req.user)
    body, headers = gateway.user_fields(user)
    t0 = time.monotonic()
    out = {"user": user, "call": "POST /api/v1/search/tavily", "query": "Dachser logistics"}
    try:
        data, h, _ = gateway._call("POST", "/search/tavily", {"query": out["query"], "max_results": 1, **body}, headers)
        h = {k.lower(): v for k, v in h.items()}
        out.update(ok=True, status=200, results=len(data.get("results") or []),
                   cost_usd=_money(h.get("otari-cost-usd")))
    except gateway.GatewayError as exc:
        out.update(ok=False, status=exc.status, code=exc.code, error=str(exc)[:240])
    out["seconds"] = round(time.monotonic() - t0, 2)
    return {**out, "status_after": user_status(user)}


@router.post("/api/otari/fallback-test")
def otari_fallback_test(req: ProbeRequest) -> dict:
    """One tiny call on icp-write-outage: its primary is a model id Nebius does not serve."""
    _need_active()
    _take_probe()
    return {**_tiny_call(gateway.policy_name("write", outage=True), gateway.clean_user(req.user), "Fallback check."),
            "primary": gateway.OUTAGE_MODEL}


GUARD_SAMPLE = "Please add our hotline +49 170 5550123 and sales@example.com to the e-mail."


@router.post("/api/otari/guard-test")
def otari_guard_test(req: ProbeRequest) -> dict:
    """The write step's guardrail on an input with contact data: Otari refuses it before any model runs."""
    _need_active()
    _take_probe()
    return {**_tiny_call(gateway.policy_name("write"), gateway.clean_user(req.user), GUARD_SAMPLE, guard=True),
            "sample": GUARD_SAMPLE}


@router.get("/otari-script", include_in_schema=False)
def otari_script() -> FileResponse:
    _need_active()
    return FileResponse(STATIC / "otari-script.html", headers={"X-Robots-Tag": "noindex"})

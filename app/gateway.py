"""Optional Mozilla Otari gateway (branch `otari`). Off unless LLM_GATEWAY=otari.

On: every model call goes to the self-hosted Otari gateway (OpenAI-compatible,
OTARI_BASE_URL, default http://127.0.0.1:8000/api/v1) with the app's service key OTARI_API_KEY.
What Otari does for the app (each one configured in scripts/otari_config.py, used here):

* routing and fallbacks: a step sends the routing policy ``icp-<step>``; Otari picks the primary
  Nebius model and, if it fails, the step's fallback. The headers Otari-Model / Otari-Fallback /
  Otari-Attempt-Count say which model answered. ``icp-write-outage`` has a primary Nebius does not
  serve, to show a real fallback on demand.
* per-user budgets: the request's ``user`` names the app's end user (demo-a, demo-b); Otari bills
  and caps each one on its own budget (header Otari-End-User-Budget picks it on first use).
* spending limit: a 1 USD/day ceiling on the service key, across all end users.
* web search and fetch: the research step calls POST /api/v1/search (Tavily behind Otari) and
  POST /api/v1/fetch (Otari's builtin fetcher) instead of Tavily directly.
* guardrails: the write step asks for the profile ``no-contact-data`` in block mode on input and
  output; Otari calls the deployment's guardrails service (this app, /otari-guardrails/validate).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

OUTAGE_MODEL = "zai-org/GLM-5.3-retired"  # an id Nebius does not serve: the primary of icp-write-outage
STANDARD_BUDGET = "user-standard"
TRIAL_BUDGET = "user-trial"
USER_BUDGETS = {"demo-a": TRIAL_BUDGET, "demo-b": STANDARD_BUDGET}  # invented demo users
DEFAULT_USER = "demo-b"
GUARD_PROFILE = "no-contact-data"
GUARD_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
GUARD_PHONE = re.compile(r"(?:\+|(?<!\d)0)\d[\d ()/-]{7,}\d")
TIMEOUT = 25


def active() -> bool:
    return os.environ.get("LLM_GATEWAY", "").strip().lower() == "otari"


def base_url() -> str:
    return os.environ.get("OTARI_BASE_URL", "").strip() or "http://127.0.0.1:8000/api/v1"


def api_key() -> str:
    return os.environ.get("OTARI_API_KEY", "").strip()


def model_name(model: str) -> str:
    return f"nebius:{model}"


def policy_name(step: str, outage: bool = False) -> str:
    return f"icp-{step}-outage" if outage else f"icp-{step}"


def clean_user(user: str | None) -> str:
    return user if user in USER_BUDGETS else DEFAULT_USER


def user_fields(user: str) -> tuple[dict, dict]:
    """(extra body, extra headers) that name the end user and the budget it starts on."""
    return {"user": user}, {"Otari-End-User-Budget": USER_BUDGETS[user]}


def guard_request() -> list[dict]:
    return [{"profile": GUARD_PROFILE, "mode": "block", "on": ["input", "output"]}]


def guard_check(text: str) -> dict:
    """The guardrails service contract Otari calls: valid False = flagged (an e-mail or phone number)."""
    hits = [k for k, rx in (("e-mail address", GUARD_EMAIL), ("phone number", GUARD_PHONE)) if rx.search(text or "")]
    return {"valid": not hits, "explanation": ("contains " + " and ".join(hits)) if hits else None,
            "score": 1.0 if hits else 0.0}


def info() -> dict:
    if not active():
        return {"name": None, "active": False}
    return {"name": "Mozilla Otari", "active": True, "self_hosted": True, "key_set": bool(api_key()),
            "provider": "nebius (Token Factory, behind the gateway)",
            "users": list(USER_BUDGETS), "default_user": DEFAULT_USER, "guard_profile": GUARD_PROFILE,
            "policies": {s: policy_name(s) for s in ("score", "plan", "write")},
            "outage_policy": policy_name("write", outage=True), "outage_model": OUTAGE_MODEL,
            "features": ["routing policies with fallback (Otari-Model / Otari-Fallback headers)",
                         "per-user budgets on a service key (demo-a, demo-b)", "1 USD/day ceiling on the key",
                         "web search and fetch through the gateway", "guardrail on the write step"]}


class GatewayError(RuntimeError):
    def __init__(self, status: int, code: str | None, detail: str):
        super().__init__(f"HTTP {status}{' ' + code if code else ''} from Otari: {detail}"[:240])
        self.status, self.code, self.detail = status, code, detail


def error_from(exc: urllib.error.HTTPError) -> GatewayError:
    try:
        body = json.loads(exc.read().decode() or "{}")
    except Exception:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, str):
        detail = json.dumps(detail)[:160] if detail else exc.reason
    return GatewayError(exc.code, (body.get("code") if isinstance(body, dict) else None)
                        or exc.headers.get("Otari-Error-Code"), str(detail)[:200])


def _call(method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[dict, dict, float]:
    req = urllib.request.Request(base_url().rstrip("/") + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {api_key()}", **(headers or {})})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}"), dict(resp.headers), time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        raise error_from(exc) from None


def end_user(user: str) -> dict | None:
    """The end user's budget and spend as Otari keeps them (GET /api/v1/end-users, end-users:read)."""
    try:
        data, _, _ = _call("GET", f"/end-users?external_id={user}")
    except Exception:
        return None
    rows = data if isinstance(data, list) else data.get("data") or []
    return rows[0] if rows else None


def end_user_budgets() -> dict:
    try:
        data, _, _ = _call("GET", "/end-user-budgets")
    except Exception:
        return {}
    rows = data if isinstance(data, list) else data.get("data") or []
    return {r.get("id") or r.get("budget_id"): r for r in rows if isinstance(r, dict)}


def key_budget() -> dict | None:
    """The service key's own ceiling (GET /api/v1/key/budget, usage:read): limit, used, left."""
    try:
        data, _, _ = _call("GET", "/key/budget")
    except Exception:
        return None
    lim = next((x for x in data.get("limits") or [] if x.get("scope") == "api_token"), None)
    return lim and {k: lim.get(k) for k in ("limit_usd", "used_usd", "remaining_usd", "resets_at")}


class OtariWeb:
    """Same interface as research.Tavily, but every call goes through Otari's search and fetch."""

    def __init__(self, budget, user: str):
        self.budget, self.user = budget, user
        self.body, self.hdrs = user_fields(user)

    def search(self, query: str, topic: str = "general", time_range: str | None = None,
               max_results: int = 3) -> tuple[list[dict], float]:
        self.budget.take_tavily()
        opts: dict = {"topic": topic, "include_raw_content": True}
        if time_range == "year":
            opts["start_date"] = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400))
        data, _, dt = _call("POST", "/search/tavily", {"query": query[:300], "max_results": max_results,
                                                       "max_tokens_per_page": 1300, "provider_options": opts,
                                                       **self.body}, self.hdrs)
        rows = [{"url": r.get("url"), "title": r.get("title"), "raw_content": r.get("snippet"),
                 "content": (r.get("snippet") or "")[:400], "published_date": r.get("date")}
                for r in data.get("results") or []]
        return rows, dt

    def extract(self, urls: list[str]) -> tuple[list[dict], float]:
        self.budget.take_tavily()
        data, _, dt = _call("POST", "/fetch/builtin_fetch", {"url": urls[0], "max_chars": 20000, **self.body},
                            self.hdrs)
        text = data.get("content") or data.get("text") or ""
        return ([{"url": data.get("final_url") or data.get("url") or urls[0], "raw_content": text}] if text else []), dt

"""Precompute LLM verdicts for the real dataset, so a public deployment needs no key.

    set -a; . ~/.config/jarvis/hackday.env; set +a     # provides NEBIUS_API_KEY
    uv run python scripts/precompute_llm.py --model openai/gpt-oss-120b
    uv run python scripts/precompute_llm.py --model zai-org/GLM-5.3-Flash --only tcsandd.com,chillspeed.co.uk --out /tmp/x.json

The key is read from LLM_API_KEY, else NEBIUS_API_KEY, held in memory only and never written.
Output: data/real/llm_scores.json with the raw model verdicts per company (status, reason, quote,
page) plus a "quote_verified" flag; the app still applies its verbatim gate and computes the tier.
Hard caps: at most --max-calls HTTP requests in total (default 100).
"""

from __future__ import annotations

import argparse
import logging
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main  # noqa: E402
from app.evidence import verify_quote  # noqa: E402
from app.llm import LLMConfig  # noqa: E402
from app.scoring import LLMScorer  # noqa: E402

NEBIUS = "https://api.tokenfactory.nebius.com/v1/"
# USD per 1M tokens (input, output), Nebius catalogue 2026-11-20; for the cost estimate only.
PRICES = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "zai-org/GLM-5.3-Flash": (0.15, 0.50),
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
}


def main_(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default=str(main.LLM_SCORES))
    ap.add_argument("--only", default="", help="comma-separated domains (for model comparisons)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=100)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    key = os.environ.get("LLM_API_KEY") or os.environ.get("NEBIUS_API_KEY")
    if not key:
        print("No LLM_API_KEY / NEBIUS_API_KEY in the environment.", file=sys.stderr)
        return 2
    base = os.environ.get("LLM_BASE_URL") or NEBIUS
    cfg = LLMConfig(base_url=base, model=a.model, api_key=key, timeout=90.0,
                    max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "4000")))

    b = main.BUNDLES["real"]
    seeds = main._seeds(b, None)
    criteria = [c for c in b.derive(seeds) if c.enabled]
    keep, _ = main._candidates(b, seeds)
    only = {d.strip() for d in a.only.split(",") if d.strip()}
    if only:
        keep = [c for c in keep if c.domain in only]
    # each company: up to 2 parse attempts x (1 + 3 retries) requests
    if len(keep) * 2 > a.max_calls:
        print(f"{len(keep)} companies could need {len(keep) * 2}+ calls > --max-calls {a.max_calls}", file=sys.stderr)
        return 2

    def one(company):
        site = b.reader.read(company)
        sc = LLMScorer(cfg)
        t0 = time.monotonic()
        raw = sc.raw(site, criteria)
        return company, site, raw, sc.client.usage, time.monotonic() - t0

    # probe with the first company: stops on auth / credit errors before fanning out
    first = one(keep[0])
    if first[2] is None:
        print(f"First call failed for {keep[0].domain}; not continuing.", file=sys.stderr)
        return 1
    keep_rest = keep[1:]
    t0 = time.monotonic() - first[4]
    with ThreadPoolExecutor(max_workers=max(1, min(a.workers, 6))) as ex:
        results = [first] + list(ex.map(one, keep_rest))
    duration = time.monotonic() - t0

    out, calls, pt, ct, failed, discarded, checked = {}, 0, 0, 0, [], 0, 0
    for company, site, raw, usage, _secs in results:
        calls += usage.calls
        pt += usage.prompt_tokens
        ct += usage.completion_tokens
        if raw is None:
            failed.append(company.domain)
            continue
        findings = []
        for f in raw.get("findings", []):
            if not isinstance(f, dict):
                continue
            rec = {k: f.get(k) for k in ("criterion_id", "status", "reason", "quote", "page")}
            if rec["status"] != "no_evidence" and rec["quote"]:
                checked += 1
                rec["quote_verified"] = verify_quote(site, rec["quote"], rec["page"]) is not None
                discarded += not rec["quote_verified"]
            elif rec["status"] != "no_evidence":
                checked += 1
                discarded += 1
                rec["quote_verified"] = False
            findings.append(rec)
        fit = raw.get("fit") if isinstance(raw.get("fit"), dict) else None
        if fit:
            fit = {k: fit.get(k) for k in ("verdict", "reason", "quote", "page")}
            fit["quote_verified"] = verify_quote(site, fit.get("quote"), fit.get("page")) is not None
        out[company.domain] = {"fit": fit, "findings": findings}

    if len(failed) == len(keep):
        print(f"All {len(keep)} companies failed ({calls} HTTP calls); nothing written. "
              "See the warnings above (e.g. HTTP 402 = credit exhausted).", file=sys.stderr)
        return 1
    pin, pout = PRICES.get(a.model, (0.0, 0.0))
    cost = round(pt / 1e6 * pin + ct / 1e6 * pout, 4)
    doc = {
        "model": a.model,
        "provider": "Nebius Token Factory" if "nebius" in base else base,
        "computed": date.today().isoformat(),
        "criteria": [c.id for c in criteria],
        "stats": {"companies": len(keep), "failed": failed, "http_calls": calls,
                  "prompt_tokens": pt, "completion_tokens": ct, "est_cost_usd": cost,
                  "price_basis_usd_per_1m": [pin, pout], "duration_s": round(duration, 1),
                  "quotes_checked": checked, "quotes_not_verbatim": discarded},
        "companies": out,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(doc["stats"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())

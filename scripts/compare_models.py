"""Compare models for the scoring step on the real snapshot (data/real/).

Same prompt, same companies, same quote gate and tiering as the app (scoring.LLMScorer).
Measures per model: latency, tokens and cost per company, share of valid JSON, share of
proposed quotes the gate discards, and tier agreement with the reference (first) model
and with the deterministic rule scorer.

    set -a; . ~/.config/jarvis/hackday.env; set +a
    uv run python scripts/compare_models.py --n 12
    uv run python scripts/compare_models.py --models zai-org/GLM-5.3-Flash@low openai/gpt-oss-120b

"model@effort" sets Nebius reasoning_effort for that model (GLM-5.3-Flash at its default spends the
whole token cap on reasoning and returns no JSON, so the app runs it at "low").

Cost guard: at most n x models calls (plus one JSON retry each); max_tokens capped.
Writes data/real/model_comparison.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import LLMConfig  # noqa: E402
from app.models_registry import NEBIUS_BASE_URL, cost_usd  # noqa: E402
from app.outreach import default_icp  # noqa: E402
from app.realdata import REAL_DIR, HttpWebsiteReader, RealDataset  # noqa: E402
from app.scoring import LLMScorer, RuleScorer, enforce_evidence, parse_findings, tier_for  # noqa: E402

OUT = REAL_DIR / "model_comparison.json"
DEFAULT_MODELS = ["zai-org/GLM-5.3-Flash@low", "nvidia/Nemotron-3_5-Lightning", "openai/gpt-oss-120b"]


def sample(ds: RealDataset, n: int):
    cands = [c for c in ds.candidates if c not in ds.existing_customers] if hasattr(ds, "candidates") else []
    if not cands:
        cands = [ds.by_domain(r["domain"]) for r in ds.candidate_records]
    cands = [c for c in cands if c]
    step = max(1, len(cands) // n)
    return cands[::step][:n]


def run_model(spec: str, sites, criteria, key: str) -> dict:
    model, _, effort = spec.partition("@")
    scorer = LLMScorer(LLMConfig(base_url=NEBIUS_BASE_URL, model=model, api_key=key, timeout=60, max_tokens=1800,
                                 reasoning_effort=effort or None))
    rows = []
    for site in sites:
        before = (scorer.client.usage.prompt_tokens, scorer.client.usage.completion_tokens)
        t0 = time.monotonic()
        raw = scorer.raw(site, criteria)
        secs = time.monotonic() - t0
        pt = scorer.client.usage.prompt_tokens - before[0]
        ct = scorer.client.usage.completion_tokens - before[1]
        row = {"domain": site.company.domain, "seconds": round(secs, 2), "prompt_tokens": pt,
               "completion_tokens": ct, "valid_json": raw is not None, "tier": None, "proposed": 0, "discarded": 0}
        if raw is not None:
            fs = parse_findings(raw, criteria)
            proposed = sum(1 for f in fs if f.status != "no_evidence" and f.quote)
            fs = enforce_evidence(site, fs)
            discarded = sum(1 for f in fs if "verdict discarded" in f.reason)
            row.update(tier=tier_for(fs, criteria)[0], proposed=proposed, discarded=discarded)
        rows.append(row)
        print(f"  {model:40s} {site.company.domain:32s} {row['seconds']:6.2f}s tier={row['tier']} "
              f"discarded={row['discarded']}/{row['proposed']}", flush=True)
    return {"model": model, "reasoning_effort": effort or None, "rows": rows, "calls": scorer.client.usage.calls,
            "errors": scorer.client.usage.errors, "reasoning_tokens": scorer.client.usage.reasoning_tokens}


def summarize(res: dict, ref: dict | None, rules: dict[str, str]) -> dict:
    rows = res["rows"]
    n = len(rows)
    ok = [r for r in rows if r["valid_json"]]
    pt = sum(r["prompt_tokens"] for r in rows)
    ct = sum(r["completion_tokens"] for r in rows)
    cost = cost_usd(res["model"], pt, ct)
    prop = sum(r["proposed"] for r in ok)
    disc = sum(r["discarded"] for r in ok)

    def agree(other: dict[str, str | None]) -> float | None:
        pairs = [(r["tier"], other.get(r["domain"])) for r in ok if other.get(r["domain"])]
        return round(sum(a == b for a, b in pairs) / len(pairs), 2) if pairs else None

    ref_tiers = {r["domain"]: r["tier"] for r in ref["rows"]} if ref else None
    return {"model": res["model"], "reasoning_effort": res["reasoning_effort"], "companies": n,
            "reasoning_tokens": res["reasoning_tokens"], "valid_json_rate": round(len(ok) / n, 2) if n else None,
            "discarded_quote_rate": round(disc / prop, 2) if prop else None, "quotes_proposed": prop,
            "avg_seconds": round(sum(r["seconds"] for r in ok) / len(ok), 2) if ok else None,
            "avg_tokens": round((pt + ct) / n) if n else None,
            "cost_per_company_usd": round(cost / n, 6) if cost is not None and n else None,
            "tier_agreement": agree(ref_tiers) if ref_tiers else 1.0,
            "tier_agreement_with_rules": agree(rules),
            "tiers": {t: sum(r["tier"] == t for r in ok) for t in "ABC"}, "errors": res["errors"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        print("NEBIUS_API_KEY is not set.", file=sys.stderr)
        return 2
    n = max(1, min(args.n, 12))
    ds = RealDataset()
    reader = HttpWebsiteReader(live=False)
    sites = [reader.read(c) for c in sample(ds, n)]
    criteria = [c for c in default_icp() if c.enabled]
    rule = RuleScorer()
    rules = {}
    for s in sites:
        fs = enforce_evidence(s, rule.findings(s, criteria))
        rules[s.company.domain] = tier_for(fs, criteria)[0]
    results = [run_model(m, sites, criteria, key) for m in args.models]
    summary = [summarize(r, results[0] if i else None, rules) for i, r in enumerate(results)]
    out = {"computed": dt.datetime.now().isoformat(timespec="minutes"), "step": "score",
           "dataset": "data/real (similarcompanies.ai snapshot, cached website texts)",
           "companies": [s.company.domain for s in sites], "reference_model": args.models[0].partition("@")[0],
           "note": (f"{n} companies from the real snapshot, same prompt and quote gate as the app. "
                    "Cost from the Nebius catalog prices of 2026-11-20."),
           "models": summary, "rows": {r["model"]: r["rows"] for r in results}}
    OUT.write_text(json.dumps(out, indent=2))
    for s in summary:
        print(json.dumps({k: s[k] for k in ("model", "valid_json_rate", "discarded_quote_rate", "avg_seconds",
                                             "avg_tokens", "cost_per_company_usd", "tier_agreement")}))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

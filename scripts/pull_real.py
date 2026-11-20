"""Pull the real dataset once, on a machine that holds a similarcompanies.ai token.

    uv run python scripts/pull_real.py search   # find_companies, at most 6 calls in total
    uv run python scripts/pull_real.py sites    # fetch public websites (robots.txt respected)
    uv run python scripts/pull_real.py build    # write data/real/companies.json
    uv run python scripts/pull_real.py reclean  # re-apply text cleaning to stored sites, offline

Token: env SIMILARCOMPANIES_TOKEN, else read at runtime from the Claude Code
credentials file (key mcpOAuth "similarcompanies|..." accessToken). The value is
kept in a variable only: never printed, logged or written anywhere.
Everything written under data/real/ is company data only.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.realdata import (  # noqa: E402
    EMAIL, PHONE, REAL_DIR, HttpWebsiteReader, SimilarCompaniesSource, company_id, filter_and_merge, reclean_snapshot,
    registrable_domain,
)
from app.sources import Company  # noqa: E402

SEEDS = [  # three small UK temperature-controlled logistics operators (domains checked by fetch)
    {"domain": "calderhead.co.uk", "name": "Calderhead"},
    {"domain": "buffaload.co.uk", "name": "Buffaload Logistics"},
    {"domain": "conwaybailey.co.uk", "name": "Conway Bailey Transport"},
]
SUMMARY = ("Provides temperature-controlled warehousing, cold storage and refrigerated road transport for "
           "chilled and frozen food. Runs its own cold stores and refrigerated fleet and delivers to "
           "retailers, food manufacturers and food service customers.")
GEO = {"countries": ["GB"]}
BUDGET = 6
TARGET = 40
SEARCH_FILE = REAL_DIR / "search.json"
CRED_FILE = Path.home() / ".claude-max20" / ".credentials.json"
SELLER = {"name": "ColdTrace (fictional)",
          "offer": "Wireless temperature sensors and automatic audit reports for temperature-controlled logistics."}


def read_token() -> str:
    tok = os.environ.get("SIMILARCOMPANIES_TOKEN", "")
    if tok:
        return tok
    creds = json.loads(CRED_FILE.read_text(encoding="utf-8"))
    for key, val in (creds.get("mcpOAuth") or {}).items():
        if key.split("|")[0] == "similarcompanies" and isinstance(val, dict) and val.get("accessToken"):
            return val["accessToken"]
    sys.exit("No similarcompanies token found.")


def load_search() -> dict:
    return json.loads(SEARCH_FILE.read_text(encoding="utf-8")) if SEARCH_FILE.exists() else {"calls": [], "raw": []}


def cmd_search() -> None:
    state = load_search()
    used = len(state["calls"])
    if used >= BUDGET:
        sys.exit(f"Budget used: {used} find_companies calls already made.")
    # first run keeps 2 calls in reserve (seed profiles, one more page)
    src = SimilarCompaniesSource(token=read_token(), max_calls=min(BUDGET - used, 4 if not used else 1))
    if state.get("search_id") and state.get("has_more"):
        # continue the saved search, by id only
        page = src._find({"search_id": state["search_id"], "more": True})
        new = page.get("companies") or []
        state["has_more"] = page.get("has_more")
    else:
        kept, stats = src.pull(SEEDS, summary=SUMMARY, geo=GEO, target=TARGET)
        new = []  # pull() keeps raw pages internally; re-run below from its log
        state["search_id"] = stats.get("search_id")
        state["has_more"] = src.log[-1]["has_more"] if src.log else None
    for entry in src.log:
        state["calls"].append({k: entry[k] for k in ("arguments", "count", "has_more", "next_steps")})
    state["raw"] += new or src._raw_pages
    SEARCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEARCH_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    kept, stats = filter_and_merge(state["raw"], SEEDS)
    print(f"find_companies calls in total: {len(state['calls'])}; raw {stats['raw']}, "
          f"seed brands dropped {stats['seed_brand']}, duplicates merged {stats['duplicate']}, kept {len(kept)}; "
          f"has_more {state['has_more']}; search_id {'yes' if state.get('search_id') else 'no'}")


def candidates() -> list[dict]:
    """Filtered list in search order, capped at TARGET."""
    kept, _ = filter_and_merge(load_search()["raw"], SEEDS)
    return kept[:TARGET]


def cmd_sites() -> None:
    reader = HttpWebsiteReader(live=True)
    todo = [(s["domain"], s["name"], f"https://{s['domain']}/") for s in SEEDS]
    todo += [(r["domain"], r["name"], r.get("website") or f"https://{r['domain']}/") for r in candidates()]
    counts: dict[str, int] = {}
    for domain, name, website in todo:
        c = Company(id=company_id(domain), name=name, domain=domain)
        if reader.load(c) is not None and "--refresh" not in sys.argv:
            snap = reader.load(c)
        else:
            snap = reader.fetch_and_store(c, website)
        counts[snap["status"]] = counts.get(snap["status"], 0) + 1
        words = sum(len(p["text"].split()) for p in snap["pages"])
        print(f"{snap['status']:<18} {domain:<34} pages {len(snap['pages'])} words {words} {snap['note'][:80]}")
    print("status counts:", counts)


def cmd_reclean() -> None:
    """Re-apply the text cleaning rules to stored site snapshots, without network."""
    for f in sorted((REAL_DIR / "sites").glob("*.json")):
        snap = json.loads(f.read_text(encoding="utf-8"))
        f.write_text(json.dumps(reclean_snapshot(snap), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("recleaned", len(list((REAL_DIR / "sites").glob("*.json"))), "site snapshots")
    left = contact_leftovers()
    if left:
        raise SystemExit(f"contact details still in snapshots: {left}")


def contact_leftovers() -> list[str]:
    """Pages under data/real/sites/ that still contain a phone number or an e-mail address."""
    out = []
    for f in sorted((REAL_DIR / "sites").glob("*.json")):
        for pg in json.loads(f.read_text(encoding="utf-8")).get("pages", []):
            if PHONE.search(pg["text"]) or EMAIL.search(pg["text"]):
                out.append(f"{f.name}{pg['path']}")
    return out


def cmd_build() -> None:
    state = load_search()
    seed_regs = {registrable_domain(s["domain"]) for s in SEEDS}
    profiles = {registrable_domain(r["domain"]): r for r in state["raw"] + state.get("seed_profiles", [])
                if registrable_domain(r.get("domain", "")) in seed_regs}
    seeds = []
    for s in SEEDS:
        p = profiles.get(registrable_domain(s["domain"]), {})
        seeds.append({"domain": s["domain"], "name": p.get("name") or s["name"], "country": p.get("country"),
                      "description": p.get("description"), "industry": p.get("industry") or [],
                      "keywords": p.get("keywords") or [], "profile_from": "similarcompanies.ai" if p else None})
    reader = HttpWebsiteReader(live=False)
    cands = []
    for r in candidates():
        snap = reader.load(Company(id=company_id(r["domain"]), name=r["name"], domain=r["domain"]))
        cands.append({k: r.get(k) for k in ("name", "domain", "website", "country", "description", "industry",
                                            "keywords", "source_domain", "alias_domains") if r.get(k) is not None}
                     | {"site_status": snap["status"] if snap else "not_fetched"})
    out = {
        "meta": {"source": "similarcompanies.ai", "snapshot": date.today().isoformat(),
                 "find_companies_calls": len(state["calls"]), "search": {"seed_companies": [s["domain"] for s in SEEDS],
                                                                           "summary": SUMMARY, "geo": GEO},
                 "evidence": "quoted from public company websites; robots.txt respected; "
                             "user agent icp-qualifier-demo/0.1",
                 "notice": "Real companies. Company data from similarcompanies.ai; seeds are examples of the "
                           "target profile, not actual customers. The seller ColdTrace is fictional."},
        "seller": SELLER,
        "seeds": seeds,
        "candidates": cands,
    }
    (REAL_DIR / "companies.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote data/real/companies.json: {len(seeds)} seeds ({sum(bool(s['profile_from']) for s in seeds)} "
          f"with similarcompanies profile), {len(cands)} candidates")


if __name__ == "__main__":
    {"search": cmd_search, "sites": cmd_sites, "build": cmd_build, "reclean": cmd_reclean}[sys.argv[1] if len(sys.argv) > 1 else "search"]()

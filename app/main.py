"""FastAPI app: ICP qualification agent (demo, no login).

Two datasets: "fictional" (canned, .example domains) and "real" (similarcompanies.ai
snapshot under data/real/, evidence from public websites). Default: real if present.
"""

from __future__ import annotations

import csv
import html
import io
import uuid
from dataclasses import dataclass
from collections import Counter, OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .evidence import collapse_ws
from .icp import derive_icp, derive_icp_real
from .models import CompanyResult, Criterion
from .realdata import REAL_DIR, HttpWebsiteReader, RealDataset, SimilarCompaniesSource, real_snapshot_available
from .outreach import router as live_router
from .otari_routes import router as otari_router
from .scoring import get_scorer, score_company
from .sources import Company, DemoCompanySource, DemoDataset, LocalWebsiteReader, remove_existing

STATIC = Path(__file__).resolve().parent.parent / "static"
LLM_SCORES = REAL_DIR / "llm_scores.json"  # precomputed by scripts/precompute_llm.py


@dataclass
class Bundle:
    key: str
    label: str
    dataset: object
    source: object
    reader: object
    notice: str
    source_line: str

    def derive(self, seeds: list[Company]) -> list[Criterion]:
        sites = [self.reader.read(s) for s in seeds]
        if self.key == "real":
            recs = [self.dataset.record(s.domain) for s in seeds]
            return derive_icp_real(sites, [r for r in recs if r], self.dataset.candidate_records)
        return derive_icp(sites)


def _fictional() -> Bundle:
    ds = DemoDataset()
    return Bundle("fictional", "fictional", ds, DemoCompanySource(ds), LocalWebsiteReader(),
                  "Demo data: all companies, domains and website texts are fictional.",
                  "Company data: canned demo answer (fictional companies, .example domains).")


def _real() -> Bundle:
    ds = RealDataset()
    snap = ds.meta["snapshot"]
    # The app always replays the snapshot: live pulls happen only in scripts/pull_real.py.
    return Bundle("real", "real (similarcompanies.ai snapshot)", ds,
                  SimilarCompaniesSource(live=False), HttpWebsiteReader(live=False),
                  "Real companies. The three seeds are examples of the target profile, not actual customers. "
                  "The seller ColdTrace is fictional.",
                  f"Company data: similarcompanies.ai, snapshot {snap}; evidence quoted from public company websites.")


BUNDLES: dict[str, Bundle] = {"fictional": _fictional()}
if real_snapshot_available():
    BUNDLES = {"real": _real(), **BUNDLES}
DEFAULT_DATASET = "real" if "real" in BUNDLES else "fictional"
# backwards-compatible module attributes (fictional demo)
dataset = BUNDLES["fictional"].dataset
reader = BUNDLES["fictional"].reader

RUNS: "OrderedDict[str, list[CompanyResult]]" = OrderedDict()
MAX_RUNS = 20

app = FastAPI(title="ICP Qualifier", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(live_router)  # /live and /api/live/*
app.include_router(otari_router)  # branch otari: /api/otari/*, /otari-guardrails/*, /otari-script


class SeedRequest(BaseModel):
    best_customers: list[str] | None = None  # domains
    dataset: str | None = None  # "real" | "fictional"; default: real if the snapshot exists


class QualifyRequest(SeedRequest):
    criteria: list[Criterion] | None = None
    scorer: str | None = None  # "llm" (default: live if LLM_* set, else precomputed) | "rules"


def _scorer(b: Bundle, mode: str | None):
    if mode not in (None, "", "llm", "rules"):
        raise HTTPException(400, "scorer must be 'llm' or 'rules'.")
    return get_scorer(mode, LLM_SCORES if b.key == "real" else None)


def _scorer_info(scorer) -> dict:
    mode = getattr(scorer, "mode", "rules")
    if mode == "live":
        line = f"LLM verdicts: {scorer.config.model}, live"
    elif mode == "precomputed":
        line = scorer.line
    else:
        line = "Rule-based scorer (keywords and regex, no LLM)"
    return {"scorer": scorer.name, "scorer_mode": mode, "scorer_line": line}


def _bundle(key: str | None) -> Bundle:
    key = key or DEFAULT_DATASET
    if key not in BUNDLES:
        raise HTTPException(400, f"Unknown dataset '{key}'. Available: {', '.join(BUNDLES)}.")
    return BUNDLES[key]


def _seeds(b: Bundle, domains: list[str] | None) -> list[Company]:
    domains = domains or b.dataset.default_best
    seeds, unknown = [], []
    for d in domains:
        c = b.dataset.by_domain(d)
        (seeds.append(c) if c else unknown.append(d))
    if unknown:
        raise HTTPException(400, f"Unknown in the {b.key} dataset: {', '.join(unknown)}. "
                                 "Pick one of the companies listed on the page.")
    if not seeds:
        raise HTTPException(400, "Give at least one best customer.")
    return seeds


def _candidates(b: Bundle, seeds: list[Company]) -> tuple[list[Company], list[Company]]:
    try:
        found = b.source.similar(seeds)
    except ValueError as exc:
        raise HTTPException(400, f"{exc} Use the three seed companies of the snapshot.") from None
    return remove_existing(found, b.dataset.existing_customers)


def _company_json(c: Company) -> dict:
    return {"id": c.id, "name": c.name, "domain": c.domain, "website": c.website, "similarity": c.similarity}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/slides", include_in_schema=False)
def slides() -> FileResponse:
    return FileResponse(STATIC / "slides.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/config")
def config(dataset: str | None = None, scorer: str | None = None) -> dict:
    b = _bundle(dataset)
    sc = _scorer(b, scorer)
    llm_default = _scorer(b, "llm")
    return {
        **_scorer_info(sc),
        "llm": getattr(sc, "mode", "") == "live",  # live model calls (key set)
        "llm_available": getattr(llm_default, "mode", "rules") != "rules",
        "dataset": b.key,
        "datasets": [{"key": k, "label": v.label} for k, v in BUNDLES.items()],
        "default_dataset": DEFAULT_DATASET,
        "company_source": b.source.name,
        "source_line": b.source_line,
        "seller": b.dataset.seller,
        "existing_customers": [_company_json(c) for c in b.dataset.existing_customers],
        "default_best_customers": b.dataset.default_best,
        "notice": b.notice,
        "real": b.key == "real",
        "size_note": ("Size: no data (similarcompanies.ai has no company size), so there is no size criterion."
                      if b.key == "real" else None),
    }


@app.post("/api/icp")
def icp(req: SeedRequest) -> dict:
    b = _bundle(req.dataset)
    seeds = _seeds(b, req.best_customers)
    criteria = b.derive(seeds)
    return {"dataset": b.key, "best_customers": [_company_json(s) for s in seeds],
            "criteria": [c.model_dump() for c in criteria]}


@app.post("/api/candidates")
def candidates(req: SeedRequest) -> dict:
    b = _bundle(req.dataset)
    seeds = _seeds(b, req.best_customers)
    keep, removed = _candidates(b, seeds)
    return {"dataset": b.key, "source": b.source.name, "source_line": b.source_line,
            "candidates": [_company_json(c) for c in keep],
            "removed_existing_customers": [_company_json(c) for c in removed]}


@app.post("/api/qualify")
def qualify(req: QualifyRequest) -> dict:
    b = _bundle(req.dataset)
    seeds = _seeds(b, req.best_customers)
    criteria = req.criteria or b.derive(seeds)
    if not any(c.enabled for c in criteria):
        raise HTTPException(400, "Enable at least one criterion.")
    keep, removed = _candidates(b, seeds)
    scorer = _scorer(b, req.scorer)
    results = [score_company(b.reader.read(c), criteria, scorer) for c in keep]  # sequential on purpose
    results.sort(key=lambda r: (r.tier, -r.score, r.name))
    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = results
    while len(RUNS) > MAX_RUNS:
        RUNS.popitem(last=False)
    return {
        "run_id": run_id,
        "dataset": b.key,
        "source_line": b.source_line,
        **_scorer_info(scorer),
        "not_a_fit": sum(r.not_a_fit for r in results),
        "tier_counts": dict(sorted(Counter(r.tier for r in results).items())),
        "removed_existing_customers": [_company_json(c) for c in removed],
        "results": [r.model_dump() for r in results],
    }


@app.get("/api/export/{run_id}.csv")
def export_csv(run_id: str, tiers: str = Query("A,B,C")) -> Response:
    results = RUNS.get(run_id)
    if results is None:
        raise HTTPException(404, "Run not found (runs are kept in memory). Run the agent again.")
    wanted = {t.strip().upper() for t in tiers.split(",") if t.strip()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["tier", "score", "company", "domain", "website", "summary", "evidence",
                "draft_opener", "status"])
    for r in results:
        if r.tier not in wanted:
            continue
        evidence = " | ".join(f"{f.label} [{f.status}]: \"{f.quote}\" <{f.url}>"
                              for f in r.findings if f.quote)
        w.writerow([r.tier, r.score, r.name, r.domain, r.website, r.summary, evidence,
                    r.draft_hook or "", "draft - human review before any outreach"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="icp-leads-{run_id}.csv"'})


@app.get("/source/{company_id}", response_class=HTMLResponse, include_in_schema=False)
def source_page(company_id: str, page: str = "/", q: str = "") -> HTMLResponse:
    b = next((x for x in BUNDLES.values() if x.dataset.by_id(company_id)), None)
    if b is None:
        raise HTTPException(404, "Unknown company")
    company = b.dataset.by_id(company_id)
    site = b.reader.read(company)
    blocks = []
    for path, text in site.pages.items():
        body = html.escape(collapse_ws(text))
        if q and path == page:
            needle = html.escape(collapse_ws(q))
            body = body.replace(needle, f'<mark id="hit">{needle}</mark>', 1)
        cls = ' class="current"' if path == page else ""
        url = html.escape(site.page_url(path))
        heading = f'<a href="{url}" target="_blank" rel="noopener">{url}</a>' if b.key == "real" else url
        blocks.append(f'<section{cls}><h2>{heading}</h2><p>{body}</p></section>')
    if b.key == "real":
        notice = (f"Text extracted from the public website ({b.source_line}) Menus, contact details and "
                  "named people are left out; quotes are verbatim.")
    else:
        notice = "Cached website text used by the agent. Fictional company, fictional domain."
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(company.name)} - cached website text</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="source-view"><main>
<p class="notice">{html.escape(notice)}</p>
<h1>{html.escape(company.name)}</h1>
{''.join(blocks) or '<p>No website text available for this company.</p>'}
</main><script>document.getElementById('hit')?.scrollIntoView({{block:'center'}});</script></body></html>"""
    return HTMLResponse(doc)

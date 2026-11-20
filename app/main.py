"""FastAPI app: ICP qualification agent (demo, fictional data, no login)."""

from __future__ import annotations

import csv
import html
import io
import uuid
from collections import Counter, OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .evidence import collapse_ws
from .icp import derive_icp
from .models import CompanyResult, Criterion
from .scoring import get_scorer, score_company
from .sources import Company, DemoCompanySource, DemoDataset, LocalWebsiteReader, remove_existing

STATIC = Path(__file__).resolve().parent.parent / "static"

dataset = DemoDataset()
source = DemoCompanySource(dataset)  # swap for a similarcompanies.ai client later
reader = LocalWebsiteReader()
RUNS: "OrderedDict[str, list[CompanyResult]]" = OrderedDict()
MAX_RUNS = 20

app = FastAPI(title="ICP Qualifier", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SeedRequest(BaseModel):
    best_customers: list[str] | None = None  # domains


class QualifyRequest(SeedRequest):
    criteria: list[Criterion] | None = None


def _seeds(domains: list[str] | None) -> list[Company]:
    domains = domains or dataset.default_best
    seeds, unknown = [], []
    for d in domains:
        c = dataset.by_domain(d)
        (seeds.append(c) if c else unknown.append(d))
    if unknown:
        raise HTTPException(400, f"Unknown in demo dataset: {', '.join(unknown)}. "
                                 "Pick one of the existing customers listed on the page.")
    if not seeds:
        raise HTTPException(400, "Give at least one best customer.")
    return seeds


def _candidates(seeds: list[Company]) -> tuple[list[Company], list[Company]]:
    return remove_existing(source.similar(seeds), dataset.existing_customers)


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
def config() -> dict:
    scorer = get_scorer()
    return {
        "scorer": scorer.name,
        "llm": scorer.name.startswith("llm:"),
        "company_source": source.name,
        "seller": dataset.seller,
        "existing_customers": [_company_json(c) for c in dataset.existing_customers],
        "default_best_customers": dataset.default_best,
        "notice": "All companies are fictional.",
    }


@app.post("/api/icp")
def icp(req: SeedRequest) -> dict:
    seeds = _seeds(req.best_customers)
    criteria = derive_icp([reader.read(s) for s in seeds])
    return {"best_customers": [_company_json(s) for s in seeds], "criteria": [c.model_dump() for c in criteria]}


@app.post("/api/candidates")
def candidates(req: SeedRequest) -> dict:
    seeds = _seeds(req.best_customers)
    keep, removed = _candidates(seeds)
    return {"source": source.name, "candidates": [_company_json(c) for c in keep],
            "removed_existing_customers": [_company_json(c) for c in removed]}


@app.post("/api/qualify")
def qualify(req: QualifyRequest) -> dict:
    seeds = _seeds(req.best_customers)
    criteria = req.criteria or derive_icp([reader.read(s) for s in seeds])
    if not any(c.enabled for c in criteria):
        raise HTTPException(400, "Enable at least one criterion.")
    keep, removed = _candidates(seeds)
    scorer = get_scorer()
    results = [score_company(reader.read(c), criteria, scorer) for c in keep]  # sequential on purpose
    results.sort(key=lambda r: (r.tier, -r.score, r.name))
    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = results
    while len(RUNS) > MAX_RUNS:
        RUNS.popitem(last=False)
    return {
        "run_id": run_id,
        "scorer": scorer.name,
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
    company = dataset.by_id(company_id)
    if company is None:
        raise HTTPException(404, "Unknown company")
    site = reader.read(company)
    blocks = []
    for path, text in site.pages.items():
        body = html.escape(collapse_ws(text))
        if q and path == page:
            needle = html.escape(collapse_ws(q))
            body = body.replace(needle, f'<mark id="hit">{needle}</mark>', 1)
        cls = ' class="current"' if path == page else ""
        blocks.append(f'<section{cls}><h2>https://{html.escape(company.domain)}{html.escape(path)}</h2><p>{body}</p></section>')
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(company.name)} - cached website text</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="source-view"><main>
<p class="notice">Cached website text used by the agent. Fictional company, fictional domain.</p>
<h1>{html.escape(company.name)}</h1>
{''.join(blocks)}
</main><script>document.getElementById('hit')?.scrollIntoView({{block:'center'}});</script></body></html>"""
    return HTMLResponse(doc)

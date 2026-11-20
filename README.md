# ICP Qualifier

> Turns a list of "similar companies" into a tiered A/B/C lead list in which every verdict carries a verbatim quote and a link to its source.

Built on 20 Nov 2026 at the AI Builders x commIT Hack Day in Berlin. Track: Go-to-Market.

**All companies, domains and website texts in this repository are fictional.** Domains use the reserved `.example` TLD. No real companies or people are used.

---

## Problem

A lookalike search (for example similarcompanies.ai) returns dozens of companies in seconds, but someone still has to open every website to decide who actually fits the ideal customer profile. An AI "fit score" without a source is a guess, and a wrong claim in a first outreach email burns the lead.

## Solution

ICP Qualifier sits between the company source and the outbound tool (for example Needle):

1. You name your three best customers. The app derives an ICP from their websites (industry terms, size range, region, buying signals) and shows it as an editable list of criteria.
2. It fetches similar companies through a `CompanySource` interface (today: a canned demo database answer) and removes existing customers.
3. The agent reads each candidate's website text and judges every criterion. Each verdict needs a **verbatim quote** plus a **link to the passage**. No quote, no verdict: the criterion shows "no evidence" and the tier drops.
4. You get a sortable, filterable list with expandable evidence and a CSV export for Needle or your CRM.

**Agent drafts, human sends.** Nothing is sent automatically; the export and the draft openers are marked as drafts.

## Start (two commands)

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12+ if needed).

```
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765> for the app and <http://127.0.0.1:8765/slides> for the slides. No login.

With an LLM key: `cp .env.example .env`, fill it in, then start with `uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8765`.

Docker (any host that sets `$PORT`):

```
docker build -t icp-qualifier .
docker run -p 8000:8000 -e PORT=8000 icp-qualifier
```

Tests: `uv run pytest -q`

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.tokenfactory.nebius.com/v1/` | empty |
| `LLM_MODEL` | Model name at that endpoint | empty |
| `LLM_API_KEY` | API key. **If any of the three is empty, the deterministic rule-based scorer is used** and the demo runs fully offline. | empty |
| `LLM_TIMEOUT_SECONDS` | Per-call timeout | `45` |
| `PORT` | Port for the Docker image | `8000` |

Keys come only from the environment. `.env` is git-ignored.

## How it works

```
 best customers ──> derive_icp() ──> editable criteria
                                          │
 CompanySource.similar() ──> remove existing customers
                                          │
            WebsiteReader.read() ──> Scorer.findings()   (RuleScorer | LLMScorer)
                                          │
                              enforce_evidence()  quote must occur verbatim in the page text,
                                          │       otherwise the verdict becomes "no evidence"
                                   tier_for()     deterministic: weights, must-have, thresholds
                                          │
                         UI list  ·  CSV export  ·  /source/<id> view with highlighted quote
```

| File | Role |
|---|---|
| `app/sources.py` | `CompanySource` and `WebsiteReader` interfaces, demo implementations |
| `app/icp.py` | ICP derivation from the best customers' websites |
| `app/scoring.py` | Rule scorer, LLM scorer, evidence gate, tiering, draft openers |
| `app/evidence.py` | Sentence splitting, quote verification, text-fragment links |
| `app/llm.py` | Minimal OpenAI-compatible client (stdlib only) |
| `app/main.py` | FastAPI routes |
| `static/` | Plain HTML/CSS/JS front end and `/slides`, no build step |
| `data/companies.json`, `data/sites/*.txt` | 28 fictional companies (3 best customers, 25 candidates incl. 2 existing customers) |

The model never sets the tier: it returns per-criterion verdicts with quotes, the app verifies the quotes and computes the tier.

### API

- `GET /api/config` - scorer mode, demo customers
- `POST /api/icp` `{"best_customers": ["kaltquell-logistik.example", ...]}` - derived criteria
- `POST /api/candidates` - similar companies minus existing customers
- `POST /api/qualify` `{"best_customers": [...], "criteria": [...]}` - tiered results with evidence (both fields optional)
- `GET /api/export/{run_id}.csv?tiers=A,B` - CSV of a run

## Status and limits

- Runs: full flow offline with the rule scorer; LLM scorer is implemented and unit-tested with a fake model, not yet against a live endpoint.
- Demo stand-ins: the company source (canned answer) and the website reader (local text files).
- Rule scorer limits: keyword and regex matching with simple sentence-level negation; it can be fooled by unusual wording.

## Next step

Plug the similarcompanies.ai API in behind `CompanySource`, fetch live websites behind `WebsiteReader`, and set the three `LLM_*` variables for an open model on Nebius.

## License

MIT, see `LICENSE`.

# ICP Qualifier

> Turns a list of "similar companies" into a tiered A/B/C lead list in which every verdict carries a verbatim quote and a link to its source.

Built on 20 Nov 2026 at the AI Builders x commIT Hack Day in Berlin. Track: Go-to-Market.

The app ships two demo datasets, switchable in the header (`?dataset=real|fictional`):

- **real** (default when `data/real/` exists): a snapshot of real UK companies from similarcompanies.ai, pulled on 2026-11-20, with evidence quoted from their public websites. See [Real data](#real-data).
- **fictional**: invented companies, domains under the reserved `.example` TLD, invented website texts. The tests use it, and it is the fallback when no snapshot exists.

The seller in both datasets, ColdTrace, is fictional. No people's names or contact details are stored.

---

## Problem

A lookalike search (for example similarcompanies.ai) returns dozens of companies in seconds, but someone still has to open every website to decide who actually fits the ideal customer profile. An AI "fit score" without a source is a guess, and a wrong claim in a first outreach email burns the lead.

## Solution

ICP Qualifier sits between the company source and the outbound tool (for example Needle):

1. You name your three best customers. The app derives an ICP from their websites (industry terms, size range, region, buying signals) and shows it as an editable list of criteria.
2. It fetches similar companies through a `CompanySource` interface (similarcompanies.ai snapshot, or the fictional demo database) and removes existing customers and seed brands.
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
| `LLM_MAX_TOKENS` | Output cap per call (reasoning models count their thinking here) | `2500` |
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
| `app/realdata.py` | `SimilarCompaniesSource` (MCP JSON-RPC, stdlib only), `HttpWebsiteReader` (robots.txt, text extraction, personal-data filter), snapshot loader |
| `app/main.py` | FastAPI routes, dataset switch |
| `scripts/pull_real.py` | Pulls the real snapshot once on a machine with a token |
| `scripts/precompute_llm.py` | Writes `data/real/llm_scores.json` (LLM verdicts for the public demo) on a machine with a key |
| `static/` | Plain HTML/CSS/JS front end and `/slides`, no build step |
| `data/companies.json`, `data/sites/*.txt` | 28 fictional companies (3 best customers, 25 candidates incl. 2 existing customers) |
| `data/real/` | Real snapshot: `search.json` (raw search results), `companies.json` (3 seeds, 40 candidates), `sites/*.json` (cleaned page texts with their URLs) |

The model never sets the tier: it returns per-criterion verdicts with quotes, the app verifies the quotes and computes the tier.

The LLM also returns a **profile check**: is the company an operator of temperature-controlled transport or storage (`candidate`), or a `publisher`, `manufacturer`, `supplier`, `spam`, `parked` domain or `other`? A non-candidate verdict needs its own verbatim quote; then the app shows the company as C with a "not a fit" badge. If the quote fails the check, the claim is ignored.

### Scorer switch and precomputed LLM verdicts

The header has a scorer switch (LLM or rule-based). For the real dataset the default is LLM: live if the three `LLM_*` variables are set, otherwise the app replays `data/real/llm_scores.json` and says so ("LLM verdicts: <model> via Nebius Token Factory, precomputed <date>"). The public deployment holds no key. The verbatim gate and the tiering run at replay time as well. The fictional dataset stays on the rule scorer unless a key is set.

The LLM path targets open models on [Nebius Token Factory](https://tokenfactory.nebius.com/) through its OpenAI-compatible endpoint. The file is written by a repeatable script; the key comes from the environment only and is never written:

```
export NEBIUS_API_KEY=...   # from the Nebius console; never put it in a file in this repo
uv run python scripts/precompute_llm.py --model openai/gpt-oss-120b
```

The script probes one company first and stops on auth or credit errors, caps the total number of calls, and records model, date, calls, tokens, estimated cost and the number of quotes that failed the verbatim check.

### API

Every route takes an optional `dataset` (`real` or `fictional`; query parameter on GET, body field on POST).

- `GET /api/config` - scorer mode, datasets, source line, demo customers
- `POST /api/icp` `{"best_customers": ["kaltquell-logistik.example", ...]}` - derived criteria
- `POST /api/candidates` - similar companies minus existing customers
- `POST /api/qualify` `{"best_customers": [...], "criteria": [...]}` - tiered results with evidence (both fields optional)
- `GET /api/export/{run_id}.csv?tiers=A,B` - CSV of a run

## Status and limits

- Runs: full flow offline with the rule scorer; LLM scorer, profile check and replay are unit-tested offline. The first live run against Nebius on 2026-11-20 was refused with HTTP 402 (account credit exhausted), so `data/real/llm_scores.json` does not exist yet and the real dataset falls back to the rule scorer until it is generated.
- Real dataset: replayed from the stored snapshot; live pulls need a token and run only through `scripts/pull_real.py`. The snapshot has no company size and no similarity score, so neither is shown or scored.
- Rule scorer limits: keyword and regex matching with simple sentence-level negation; it can be fooled by unusual wording.

## Real data

The real dataset is pulled once on a machine that holds a similarcompanies.ai token and stored in `data/real/`. The app only replays that snapshot, so a public deployment needs no token and makes no calls to similarcompanies.ai or to company websites.

```
uv run python scripts/pull_real.py search   # find_companies, at most 6 calls in total, paged only by search_id
uv run python scripts/pull_real.py sites    # fetch public websites: homepage + up to 2 about/product pages
uv run python scripts/pull_real.py build    # write data/real/companies.json
uv run python scripts/pull_real.py reclean  # re-apply text cleaning to stored pages, offline
```

- Token: read at runtime from `SIMILARCOMPANIES_TOKEN`, else from the local Claude Code MCP credentials. It is held in a variable only; it is never printed, logged or written to a file, and there is no token in this repository.
- Search: three seed companies (small UK temperature-controlled logistics operators) plus a short profile text, limited to Great Britain. Seed brands and side domains are removed and results are deduplicated by registrable domain.
- Websites: user agent `icp-qualifier-demo/0.1`, robots.txt respected, 8 s timeout. Contact, team, legal, careers, news and review pages are never fetched. Emails, phone numbers, sentences naming people and quoted testimonials are removed from the stored text. Each text keeps its page URL, so evidence links go to the real page with a text fragment.
- ICP: industry terms are taken from the similarcompanies.ai industry and keyword fields and kept if at least two of the three seed websites use them. similarcompanies.ai returned no profile for the seeds themselves, so the terms are counted on the seed websites.
- The seeds are examples of the target profile, not actual customers.

## Next step

Run the source and website reader live per request, add a source for company size, and generate `data/real/llm_scores.json` once the Nebius account has credit.

## License

MIT, see `LICENSE`.

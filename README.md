# ICP Qualifier

> Type any company. An agent researches it on the live web, scores it against your ideal customer
> profile with verbatim quotes, plans the outreach and drafts a first email. A person decides what goes out.

## Try it in one minute

1. Open `/live`.
2. Type a company domain or name and press "Check this company".
3. Watch four steps: research (Tavily), score, plan, draft (three models on Nebius Token Factory).
   Each step shows its model, seconds, tokens and cost.
4. Click any quote to open its source. Edit the draft. Nothing is sent.

No key? The page replays a recorded run and says so (`data/live_example.json`, written by `scripts/record_live_example.py`).

## Who it is for, and what changes

An SDR or founder doing outbound. Before: ten tabs per company, a guess, an email written from memory.
After: one input, a verdict with quotes that can be checked in one click, and a draft that opens with
one sourced fact. Companies that do not fit are stopped before anyone writes to them.
Time and cost per company: about 20 seconds and 6 to 9 cents with web research (totals line of each run; measured per run, not a benchmark).
Recorded example (`data/live_example.json`, dachser.de, European logistics profile): Tier A in 16 s for $0.09
(model calls $0.054 plus 4 Tavily credits), 0 of 6 quotes rejected, German draft of 48 words.

## Which model does which step, and why (summary)

| Step | Model | Why |
|---|---|---|
| Research | Tavily search + extract | Live web, every source keeps its URL |
| Score | `zai-org/GLM-5.3-Flash` | Many calls, JSON out, code checks every quote: pay for speed |
| Plan | `moonshotai/Kimi-K3` | One call per company, judgement matters: pay for reasoning |
| Write | `zai-org/GLM-5.3` | Natural voice in the customer's language |

Comparison on the scoring step: <!--CMP-->measured on 12 companies of the real snapshot, same prompt and quote gate as the app (`data/real/model_comparison.json`, 2026-11-20 12:39):

| Model | Seconds / company | USD / company | Valid JSON | Quotes discarded |
|---|---|---|---|---|
| `zai-org/GLM-5.3-Flash` (reasoning low) | 3.34 | 0.000394 | 100% | 3% of 36 |
| `openai/gpt-oss-120b` | 1.78 | 0.000574 | 100% | 29% of 35 |
| `nvidia/Nemotron-3_5-Lightning` | - | 0.001053 | 0% | - (no valid JSON) |

GLM-5.3-Flash is the scoring model: the cheapest, and the gate threw out 1 of its 36 quotes (3%) against 10 of 35 (29%) for gpt-oss-120b. gpt-oss-120b is faster (1.78 s against 3.34 s); we pay that time for fewer lost verdicts. Nemotron returned no valid JSON. Tier agreement is left out here: it is measured against GLM-5.3-Flash itself, so it shows disagreement, not who is right.

The lead list (`/`) was scored earlier with gpt-oss-120b, and there the gate threw out only 4 of 121 quotes (3%). The runs differ in more than the model: 40 companies instead of 12, reasoning effort at the model default instead of "low", and a 4000-token cap instead of 1800 (`data/real/llm_scores.json`, `scripts/precompute_llm.py`). We have not measured which of these explains the gap; our guess is the reasoning effort.<!--/CMP-->

## Limits (summary)

Built entirely on 20 Nov 2026 at the event; nothing existed before. No sending, no CRM write-back,
no contact finding. The quote check proves that a sentence is in the source, not that the source is right.
Germany: a cold email or LinkedIn message needs prior express consent, also in B2B (UWG § 7 (2) no. 2);
for German companies the plan recommends a phone call with a specific business reason or a letter. Other
countries differ (UK: email to limited companies with sender identity and opt-out; not to sole traders).
The app warns, it does not check consent. Not legal advice. Sentences naming people are dropped from research by a heuristic
filter; the plan addresses roles only. The lead-list page (`/`) uses a stored snapshot of 40 UK
companies, not a live feed.

Built on 20 Nov 2026 at the AI Builders x commIT Hack Day in Berlin. Track: Go-to-Market.

The app ships two demo datasets, switchable in the header (`?dataset=real|fictional`):

- **real** (default when `data/real/` exists): a snapshot of real UK companies from similarcompanies.ai, pulled on 2026-11-20, with evidence quoted from their public websites. See [Real data](#real-data).
- **fictional**: invented companies, domains under the reserved `.example` TLD, invented website texts. The tests use it, and it is the fallback when no snapshot exists.

The seller in both datasets, ColdTrace, is fictional. No people's names or contact details are stored.

---

## Problem

A lookalike search (for example similarcompanies.ai) returns dozens of companies in seconds, but someone still has to open every website to decide who actually fits the ideal customer profile. An AI "fit score" without a source is a guess: in our test on 12 companies, one open model had 10 of its 35 proposed quotes (29%) thrown out because they were not in the source.

## Solution

ICP Qualifier sits between the company source and the outbound tool (for example Needle):

1. You name your three best customers. The app derives an ICP from their websites (industry terms, size range, region, timing signals in public text) and shows it as an editable list of criteria.
2. It fetches similar companies through a `CompanySource` interface (similarcompanies.ai snapshot, or the fictional demo database) and removes existing customers and seed brands.
3. The agent reads each candidate's website text and judges every criterion. Each verdict needs a **verbatim quote** plus a **link to the passage**. No quote, no verdict: the criterion shows "no evidence" and the tier drops.
4. You get a sortable, filterable list with expandable evidence and a CSV export for Needle or your CRM.

**Agent drafts, a person decides.** Nothing is sent automatically; the export and the draft openers are marked as drafts.

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

- Runs: full flow offline with the rule scorer; LLM scorer, profile check and replay are unit-tested offline. The first live run against Nebius on 2026-11-20 was refused with HTTP 402 (account credit exhausted); after credit was added, `data/real/llm_scores.json` was generated the same day with `openai/gpt-oss-120b`: 40 companies, 40 calls, estimated cost USD 0.0447, 121 quotes checked, 4 discarded as not verbatim (figures from the file's `stats` block).
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

## Live agent (`/live`)

Open <http://127.0.0.1:8765/live>. Enter your product in one sentence, keep or edit the ICP (pre-filled with the cold-chain ICP the lead list uses) and type **any** company domain or name. The agent runs four visible steps; each shows its model, duration, tokens and cost:

1. **Research** (Tavily): the company's own website (extract) plus three targeted searches: what the company does, news of the last 12 months, sites and size. Hits that do not name the company, social profiles and pages about individual people are dropped; sentences naming people are removed. Each source keeps its URL.
2. **Score** (small model): a verdict per ICP criterion with a verbatim quote and the source number. The existing quote gate (`scoring.enforce_evidence`) discards any quote that is not in the fetched text and the page shows it as discarded. The app computes the A/B/C tier (`scoring.tier_for`), not the model.
3. **Plan** (reasoning model): whom to address (roles only; the agent never names or searches people), the hook, the channel, what not to say. Every fact cites `[n]`.
4. **Draft** (writing model): a short email in the customer's language (German for German sites, English otherwise), every fact with `[n]`. Each `[n]` is a link to the source. References to missing sources and sentences with numbers but no source are flagged. The draft sits in an editable text box with **Copy** and **Discard**; the app sends nothing.

Guards for a public demo: per run at most 5 Tavily calls and 6 model calls (one second try per step on invalid JSON or overload), `max_tokens` and a timeout per call, at most 60 runs per process and day, input capped at 200 characters. Web text and form input go to the models inside `<untrusted>` blocks with the instruction never to follow instructions found there. Without `NEBIUS_API_KEY` and `TAVILY_API_KEY` the page says "live mode needs NEBIUS_API_KEY and TAVILY_API_KEY" and plays back a recorded run (`data/live_example.json`, marked as recorded). An empty Nebius balance shows as "Nebius account has no credit".

## Who needs this and how they do it today

A founder or the first GTM hire at a B2B startup who has a target account and twenty minutes. Today they open the company website, search for news, guess whether the account fits, look for a reason to write, and write the email from scratch, or paste the domain into a chatbot that returns confident text with no sources.

## What gets easier

One input, about a minute, and the result is checkable: every ICP verdict has a quote that provably appears in a fetched page, every fact in the plan and the draft links to its source, and claims without a source are flagged. Before: open ten tabs, decide on gut feeling, write from a blank page. After: read the sourced verdicts, accept or fix the plan, edit the draft, send it yourself.

## Which model does which step, and why

All model calls go through Nebius Token Factory; web research goes through Tavily. The mapping lives in one file, `app/models_registry.py`, and each step can be overridden with `LIVE_MODEL_SCORE`, `LIVE_MODEL_PLAN`, `LIVE_MODEL_WRITE`. Prices are from the Nebius catalog on 2026-11-20 (USD per 1M tokens in/out).

| Step | Model | Why |
|---|---|---|
| Research | Tavily search + extract | Live web results with URLs; the website extract gives the text the quote gate checks against. |
| Score | `zai-org/GLM-5.3-Flash` (0.15 / 0.50) | Small, fast, cheap: one call per company, JSON out, and code checks every quote anyway. |
| Plan | `moonshotai/Kimi-K3` (3 / 15) | Strong reasoning, one call per run: choosing whom to address and which hook needs judgement, the volume is low. |
| Write | `zai-org/GLM-5.3` (1.40 / 4.40) | Fluent multilingual writer, fast enough for a live demo, cheaper than the planner. |

Comparison of models for the scoring step: `uv run python scripts/compare_models.py --n 12` scores 12 companies of the real snapshot with each model (same prompt and quote gate as the app) and writes `data/real/model_comparison.json`: latency, tokens and cost per company, share of valid JSON, share of discarded quotes, tier agreement. `/live` shows the table.

## Limits: what works, what is unfinished, what existed before the event

- Everything in this repository was built on 20 Nov 2026 at the hack day. Nothing existed before the event except the public APIs (Tavily, Nebius, similarcompanies.ai).
- Works: live Tavily research on any company, quote gate, app-computed tier, plan and draft with clickable sources, caps, recorded fallback, offline tests with mocked Tavily and Nebius.
- Unfinished: model numbers depend on the Nebius account having credit; see `data/real/model_comparison.json` for what was actually measured. Language detection is a heuristic (TLD and stop words). The "no source" check for the draft only catches sentences with numbers. Search hits are filtered by name match, so a company with a common name can pull in a namesake.
- The lead list uses a snapshot (similarcompanies.ai, 2026-11-20); the ICP criteria come from heuristics over three seed websites. The seller ColdTrace is fictional.

## Try it

```
uv sync
set -a; . /path/to/your.env; set +a     # NEBIUS_API_KEY and TAVILY_API_KEY
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/live>, type a domain (for example `chillspeed.co.uk`, `stef.com`, or a German company such as `nagel-group.com`) and press **Check this company**. Without keys the page plays the recorded run, if `data/live_example.json` exists. Tests: `uv run pytest -q` (no network).

## Next step

Batch mode: score 50 companies with the small model, plan and write only for tier A. Run the source and website reader live per request, and add a source for company size.

## License

MIT, see `LICENSE`.

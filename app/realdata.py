"""Real dataset: companies from similarcompanies.ai, evidence from their public websites.

Two modes, chosen per object:

* live      ``SimilarCompaniesSource`` calls the similarcompanies.ai MCP server over
            plain HTTP (JSON-RPC ``tools/call``) with a bearer token;
            ``HttpWebsiteReader`` fetches public pages (robots.txt respected).
            Both write their results as a snapshot under ``data/real/``.
* snapshot  no token, no network: both replay ``data/real/``. This is what the
            public demo runs.

Only company data is kept. Contact, imprint and team pages are never fetched;
e-mail addresses, phone numbers and sentences that name a person's role are
dropped from the extracted text.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from .sources import DATA_DIR, Company, Website, normalize_domain

REAL_DIR = DATA_DIR / "real"
MCP_URL = "https://similarcompanies.ai/mcp"
USER_AGENT = "icp-qualifier-demo/0.1"
TIMEOUT = 8.0
MAX_EXTRA_PAGES = 2
MAX_PAGE_BYTES = 3_000_000
MAX_PAGE_WORDS = 2500

# Second-level public suffixes we may meet; enough for a registrable-domain guess.
_SECOND_LEVEL = {
    "co.uk", "org.uk", "ltd.uk", "plc.uk", "me.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.za",
    "com.br", "com.mx", "co.jp", "co.kr", "com.cn", "com.sg", "com.tr", "co.in", "com.pl", "co.il",
    "com.es", "com.ar", "co.at", "or.at",
}


def registrable_domain(host: str) -> str:
    """``shop.example.co.uk`` -> ``example.co.uk`` (heuristic, no public-suffix list)."""
    host = normalize_domain(host).split(":")[0].strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def brand_stem(domain: str) -> str:
    """``buffaload.co.uk`` -> ``buffaload``."""
    return registrable_domain(domain).split(".")[0]


def company_id(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", registrable_domain(domain)).strip("-")


def _norm_name(name: str) -> str:
    name = re.sub(r"\b(ltd|limited|plc|gmbh|ag|bv|inc|llc|group|logistics|transport)\b\.?", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


# ---------------------------------------------------------------- similarcompanies.ai

class MCPError(RuntimeError):
    pass


def _mcp_post(token: str, method: str, params: dict, req_id: int) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}).encode()
    req = urllib.request.Request(MCP_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:  # never echo request headers
        raise MCPError(f"HTTP {exc.code} from similarcompanies.ai") from None
    if "text/event-stream" in ctype:
        data = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        raw = data[-1] if data else "{}"
    msg = json.loads(raw)
    if "error" in msg:
        raise MCPError(f"MCP error: {msg['error'].get('message')}")
    return msg.get("result", {})


def call_tool(token: str, name: str, arguments: dict, req_id: int = 1) -> dict:
    result = _mcp_post(token, "tools/call", {"name": name, "arguments": arguments}, req_id)
    if result.get("isError"):
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        raise MCPError(f"{name} failed: {text[:300]}")
    for part in result.get("content", []):
        if part.get("type") == "text":
            try:
                return json.loads(part["text"])
            except json.JSONDecodeError:
                return {"text": part["text"]}
    return result.get("structuredContent") or {}


def filter_and_merge(records: list[dict], seeds: list[dict]) -> tuple[list[dict], dict]:
    """Drop the seed brands (and their side domains), merge duplicates by registrable domain."""
    seed_regs = {registrable_domain(s["domain"]) for s in seeds}
    seed_stems = {brand_stem(s["domain"]) for s in seeds}
    seed_names = {_norm_name(s.get("name") or "") for s in seeds} - {""}
    stats = {"raw": len(records), "seed_brand": 0, "duplicate": 0}
    out: dict[str, dict] = {}
    for r in records:
        hosts = [r.get("domain") or ""]
        if r.get("website"):
            hosts.append(urllib.parse.urlsplit(r["website"]).hostname or "")
        hosts = [h for h in hosts if h]
        regs = {registrable_domain(h) for h in hosts}
        stems = {brand_stem(h) for h in hosts}
        if regs & seed_regs or stems & seed_stems or _norm_name(r.get("name") or "") in seed_names:
            stats["seed_brand"] += 1
            continue
        # canonical domain: the website the company actually answers on
        canon = registrable_domain(hosts[-1])
        nm = _norm_name(r.get("name") or "")
        same_name = next((k for k, v in out.items() if nm and _norm_name(v.get("name") or "") == nm), None)
        if same_name and canon not in out:  # one company under two domains, e.g. brand.com and brand.co.uk
            stats["duplicate"] += 1
            out[same_name].setdefault("alias_domains", []).append(canon)
            continue
        if canon in out:
            stats["duplicate"] += 1
            out[canon].setdefault("alias_domains", []).append(normalize_domain(r.get("domain") or ""))
            continue
        out[canon] = {**r, "domain": canon, "source_domain": normalize_domain(r.get("domain") or "")}
    return list(out.values()), stats


class SimilarCompaniesSource:
    """``CompanySource`` backed by similarcompanies.ai.

    Live when a token is given (argument, else env ``SIMILARCOMPANIES_TOKEN``) and
    ``live`` is not False; otherwise replays ``data/real/companies.json``.
    """

    name = "similarcompanies.ai"

    def __init__(self, data_dir: Path = REAL_DIR, token: str | None = None, live: bool | None = None,
                 max_calls: int = 6):
        self.data_dir = data_dir
        self._token = token if token is not None else os.environ.get("SIMILARCOMPANIES_TOKEN", "")
        self.live = bool(self._token) if live is None else (live and bool(self._token))
        self.max_calls = max_calls
        self.calls_used = 0
        self.log: list[dict] = []  # per call: arguments without secrets, count, has_more
        self._raw_pages: list[dict] = []  # every company record returned by the last pull()

    # ---------------- snapshot
    def snapshot(self) -> dict:
        return json.loads((self.data_dir / "companies.json").read_text(encoding="utf-8"))

    def _to_company(self, r: dict) -> Company:
        return Company(id=company_id(r["domain"]), name=r["name"], domain=r["domain"], similarity=None)

    def similar(self, seeds: list[Company], limit: int = 50) -> list[Company]:
        if not self.live:
            snap = self.snapshot()
            seed_set = {registrable_domain(s.domain) for s in seeds}
            if seed_set != {registrable_domain(s["domain"]) for s in snap["seeds"]}:
                raise ValueError("The real snapshot was pulled for other seed companies.")
            return [self._to_company(r) for r in snap["candidates"]][:limit]
        records, _ = self.pull([{"domain": s.domain, "name": s.name} for s in seeds], target=limit)
        return [self._to_company(r) for r in records][:limit]

    # ---------------- live
    def _find(self, arguments: dict) -> dict:
        if self.calls_used >= self.max_calls:
            raise MCPError("find_companies call budget used up")
        if arguments.get("more") and not arguments.get("search_id"):
            raise MCPError("refusing more: true without search_id")
        self.calls_used += 1
        page = call_tool(self._token, "find_companies", arguments, req_id=self.calls_used)
        self.log.append({"arguments": arguments, "count": page.get("count"),
                         "has_more": page.get("has_more"), "next_steps": page.get("next_steps")})
        return page

    def _search_id(self, seed_domains: list[str]) -> str | None:
        listing = call_tool(self._token, "list_searches", {}, req_id=100 + self.calls_used)
        searches = listing.get("searches") or listing.get("items") or []
        for s in searches:  # newest first; take the current one or the first matching our seeds
            params = json.dumps(s.get("parameters") or s.get("params") or {})
            if s.get("current") or s.get("is_current") or all(d in params for d in seed_domains):
                return s.get("search_id") or s.get("id")
        return None

    def pull(self, seeds: list[dict], summary: str | None = None, geo: dict | None = None,
             target: int = 40) -> tuple[list[dict], dict]:
        """Run one search and page through it (by search_id) until ``target`` companies survive the filter."""
        if not self.live:
            raise MCPError("no token: live pull not possible")
        seed_domains = [normalize_domain(s["domain"]) for s in seeds]
        args: dict = {"seed_companies": seed_domains[:6]}
        if summary:
            args["summary"] = summary
        if geo:
            args["geo"] = geo
        raw: list[dict] = []
        page = self._find(args)
        raw += page.get("companies") or []
        seed_records = [r for r in raw if registrable_domain(r.get("domain", "")) in
                        {registrable_domain(d) for d in seed_domains}]
        search_id = page.get("search_id")
        kept, stats = filter_and_merge(raw, seeds)
        while len(kept) < target and page.get("has_more") and self.calls_used < self.max_calls:
            search_id = search_id or self._search_id(seed_domains)
            if not search_id:
                break  # never send more: true without an id
            page = self._find({"search_id": search_id, "more": True})
            raw += page.get("companies") or []
            kept, stats = filter_and_merge(raw, seeds)
        self._raw_pages = raw
        stats.update({"kept": len(kept), "find_companies_calls": self.calls_used, "search_id": search_id,
                      "seed_records_returned": len(seed_records)})
        return kept, stats


# ---------------------------------------------------------------- websites

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "footer", "form", "iframe",
              "select", "button", "aside", "canvas", "object", "title"}
# visually hidden helpers ("Skip to content"): not visible text, a text fragment could not find them
_HIDDEN_CLASS = re.compile(r"screen-reader|sr-only|visually-hidden|skip-link|cookie", re.I)
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "header", "li", "ul", "ol", "h1", "h2", "h3",
               "h4", "h5", "h6", "br", "tr", "td", "th", "table", "blockquote", "dd", "dt", "figcaption",
               "address", "hr"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "track"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.skip = 0
        self.links: list[str] = []
        self.lang = ""

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.lines.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = (a.get("lang") or "").lower()
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in _VOID:
            if tag in _BLOCK_TAGS:
                self._flush()
            return
        if tag in _SKIP_TAGS or a.get("aria-hidden") == "true" or "cookie" in (a.get("id") or "").lower() \
                or _HIDDEN_CLASS.search(a.get("class") or ""):
            self.skip += 1
            self._stack.append((tag, True))
            return
        self._stack.append((tag, False))
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        # pop up to the matching open tag; tolerates unclosed <p>, <li> and friends
        if any(t == tag for t, _ in self._stack):
            while self._stack:
                t, was_skip = self._stack.pop()
                if was_skip:
                    self.skip -= 1
                if t == tag:
                    break
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def feed_all(self, html_text: str) -> "_TextExtractor":
        self._stack: list[tuple[str, bool]] = []
        self.feed(html_text)
        self.close()
        self._flush()
        return self


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+|\b0)\d[\d ()/-]{7,}\d")
PERSON_ROLE = re.compile(
    r"\b(CEO|CFO|COO|CTO|MD|founder|co-founder|managing director|director|directors|chairman|chairwoman|"
    r"head of|manager|proprietor|stewardship|grandson|granddaughter|brother|sister|son|daughter|wife|husband|"
    r"said|says|commented|Mr|Mrs|Ms|Miss|Dr)\b\.?", re.IGNORECASE)
PERSON_BY = re.compile(r"\b(?:by|with|of) (?:[A-Z]\.\s?)*[A-Z][a-z]+(?:\s[A-Z][a-z]+)+")
# Common given names: a sentence naming one is dropped (people are not company data).
GIVEN_NAMES = set("""
aaron adam adrian alan albert alex alexander alice alison amanda amy andrew andy angela anna anne anthony
barry ben benjamin bernard bill bob brian bruce carl carol caroline catherine charles charlie charlotte chris
christine christopher claire colin craig dan daniel danny darren dave david dean debbie dennis derek diane
donald doug douglas duncan ed edward elizabeth emily emma eric fiona frank fred gareth gary gavin geoff
george gordon graham grant greg hannah harry helen henry ian jack jacob james jamie jane janet jason jeff
jennifer jenny jessica jim jimmy joan joe john jon jonathan joseph josh julia julie justin karen kate katie
keith kelly ken kevin kim laura lee leon linda lisa louise lucy luke malcolm marc margaret maria mark martin
mary matt matthew michael michelle mike neil nick nicola nigel oliver paul pauline peter phil philip rachel
ray rebecca richard rob robert roger ron ross roy russell ryan sam samuel sandra sarah scott sean sharon simon
sophie stephen steve steven stuart sue susan terry thomas tim tom tony tracy trevor victoria wallace wayne
william""".split())
# biographies: a sentence about "he"/"she" is about a person, not the company
PRONOUN = re.compile(r"\b(he|she|his|her|hers|him|himself|herself)\b", re.IGNORECASE)
SURNAME_FAMILY = re.compile(r"\b(?:the )?[A-Z][a-z]+ family\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _personal(sentence: str) -> bool:
    if EMAIL.search(sentence) or PHONE.search(sentence) or PERSON_ROLE.search(sentence):
        return True
    if PERSON_BY.search(sentence) or PRONOUN.search(sentence) or SURNAME_FAMILY.search(sentence):
        return True
    if sentence.lstrip()[:1] in "\"“‘'" or "“" in sentence or "”" in sentence:
        return True  # quoted testimonial
    if re.search(r"[–—-]\s*[A-Z][a-z]+\W*$", sentence):  # "... - Allan" attribution
        return True
    return any(w.lower() in GIVEN_NAMES for w in re.findall(r"\b[A-Z][a-z]+\b", sentence))


def clean_lines(lines: list[str]) -> list[str]:
    """Drop menu fragments and anything personal (e-mails, phones, named people, testimonials).

    A line keeps its wording; if one of its sentences is dropped, the remaining
    sentences go on separate lines so no quote can span the gap.
    """
    out, seen = [], set()
    for ln in lines:
        sents = SENT_SPLIT.split(ln)
        keep = [x for x in sents if not _personal(x)]
        pieces = [ln] if len(keep) == len(sents) else keep
        for piece in pieces:
            piece = piece.strip()
            if len(piece.split()) < 3 or piece in seen or "read more" in piece.lower():
                continue
            seen.add(piece)
            out.append(piece)
    words, kept = 0, []
    for ln in out:
        words += len(ln.split())
        if words > MAX_PAGE_WORDS:
            break
        kept.append(ln)
    return kept


def reclean_snapshot(snap: dict) -> dict:
    """Re-apply the current cleaning rules to a stored site snapshot (no network)."""
    pages = []
    for pg in snap.get("pages", []):
        path = pg["path"]
        if path != "/" and FORBIDDEN.search(path):
            continue
        text = "\n".join(clean_lines(pg["text"].split("\n")))
        if text.strip():
            pages.append({**pg, "text": text})
    return {**snap, "pages": pages}


def html_to_text(html_text: str) -> tuple[str, list[str], str]:
    ex = _TextExtractor().feed_all(html_text)
    return "\n".join(clean_lines(ex.lines)), ex.links, ex.lang


WANTED = re.compile(r"about|who-we-are|our-company|company|product|solution|service|what-we-do", re.I)
FORBIDDEN = re.compile(
    r"contact|kontakt|impressum|imprint|legal|privacy|datenschutz|cookie|terms|team|people|staff|leader|"
    r"management|board|director|career|job|vacanc|recruit|login|account|cart|basket|news|blog|press|"
    r"event|download|document|review|testimonial|\.(pdf|jpe?g|png|zip|docx?)$", re.I)


def pick_extra_pages(base_url: str, links: list[str], limit: int = MAX_EXTRA_PAGES) -> list[str]:
    """At most ``limit`` same-site about/product/solution/service pages; one 'about' first if present."""
    base_reg = registrable_domain(urllib.parse.urlsplit(base_url).hostname or "")
    cands: list[str] = []
    for href in links:
        url = urllib.parse.urljoin(base_url, href.strip())
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if registrable_domain(parts.hostname) != base_reg:
            continue
        path = parts.path or "/"
        if path in ("/", "") or not WANTED.search(path) or FORBIDDEN.search(path):
            continue
        if path.count("/") > 3:  # stay near the top of the site
            continue
        clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        if clean.rstrip("/") not in {c.rstrip("/") for c in cands} and clean.rstrip("/") != base_url.rstrip("/"):
            cands.append(clean)
    about = [c for c in cands if re.search(r"about|who-we-are|our-company|company", c, re.I)]
    rest = [c for c in cands if c not in about]
    ordered = about[:1] + rest + about[1:]
    return ordered[:limit]


@dataclass
class FetchResult:
    status: str  # ok | robots_disallowed | unreachable | not_html
    pages: list[dict] = field(default_factory=list)
    note: str = ""


class HttpWebsiteReader:
    """``WebsiteReader`` for real websites.

    live=True: fetch homepage + up to 2 about/product/solution pages, respect robots.txt,
    write ``data/real/sites/<id>.json``. live=False: read those files only, no network.
    """

    def __init__(self, data_dir: Path = REAL_DIR, live: bool = False, delay: float = 0.5):
        self.sites_dir = data_dir / "sites"
        self.live = live
        self.delay = delay
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._cache: dict[str, Website] = {}

    # ---------------- snapshot
    def _path(self, company: Company) -> Path:
        return self.sites_dir / f"{company.id}.json"

    def load(self, company: Company) -> dict | None:
        p = self._path(company)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def read(self, company: Company) -> Website:
        if company.id in self._cache:
            return self._cache[company.id]
        snap = self.load(company)
        if snap is None and self.live:
            snap = self.fetch_and_store(company)
        pages, urls = {}, {}
        for pg in (snap or {}).get("pages", []):
            pages[pg["path"]] = pg["text"]
            urls[pg["path"]] = pg["url"]
        site = Website(company=company, pages=pages, urls=urls)
        self._cache[company.id] = site
        return site

    # ---------------- live
    def _get(self, url: str) -> tuple[int, str, str, str]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                                   "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_PAGE_BYTES)
            ctype = resp.headers.get("Content-Type", "")
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, resp.geturl(), ctype, body.decode(charset, "replace")

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                _, _, _, text = self._get(origin + "/robots.txt")
                rp.parse(text.splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) or exc.code >= 500:
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except Exception:
                rp = None  # robots.txt unreachable: we do not guess, the site is skipped
            self._robots[origin] = rp
        return self._robots[origin]

    def _allowed(self, url: str) -> bool | None:
        rp = self._robots_for(url)
        if rp is None:
            return None
        return rp.can_fetch(USER_AGENT, url)

    def fetch(self, company: Company, website: str | None = None) -> FetchResult:
        start = website or company.website
        allowed = self._allowed(start)
        if allowed is None:
            return FetchResult("unreachable", note="robots.txt not reachable")
        if not allowed:
            return FetchResult("robots_disallowed", note=f"robots.txt disallows {start}")
        try:
            _, final, ctype, body = self._get(start)
        except Exception as exc:
            return FetchResult("unreachable", note=type(exc).__name__)
        if "html" not in ctype:
            return FetchResult("not_html", note=ctype)
        if final != start and self._allowed(final) is False:
            return FetchResult("robots_disallowed", note=f"robots.txt disallows {final}")
        text, links, lang = html_to_text(body)
        pages = [{"path": urllib.parse.urlsplit(final).path or "/", "url": final, "text": text}]
        skipped = []
        for url in pick_extra_pages(final, links):
            if not self._allowed(url):
                skipped.append(f"{url} (robots)")
                continue
            time.sleep(self.delay)
            try:
                _, f2, ct2, b2 = self._get(url)
            except Exception as exc:
                skipped.append(f"{url} ({type(exc).__name__})")
                continue
            if "html" not in ct2:
                continue
            path = urllib.parse.urlsplit(f2).path or "/"
            if any(p["path"] == path for p in pages) or FORBIDDEN.search(path):
                continue
            pages.append({"path": path, "url": f2, "text": html_to_text(b2)[0]})
        pages = [p for p in pages if p["text"].strip()]
        note = "; ".join(filter(None, [f"lang={lang}" if lang else "", *skipped]))
        return FetchResult("ok" if pages else "not_html", pages, note)

    def fetch_and_store(self, company: Company, website: str | None = None) -> dict:
        res = self.fetch(company, website)
        snap = {"id": company.id, "domain": company.domain, "fetched": date.today().isoformat(),
                "user_agent": USER_AGENT, "status": res.status, "note": res.note, "pages": res.pages}
        self.sites_dir.mkdir(parents=True, exist_ok=True)
        self._path(company).write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return snap


# ---------------------------------------------------------------- dataset

class RealDataset:
    """Same surface as ``DemoDataset`` for the similarcompanies.ai snapshot."""

    def __init__(self, data_dir: Path = REAL_DIR):
        raw = json.loads((data_dir / "companies.json").read_text(encoding="utf-8"))
        self.meta = raw["meta"]
        self.seller = raw["seller"]
        self.seed_records = raw["seeds"]
        self.candidate_records = raw["candidates"]
        to_c = lambda r: Company(id=company_id(r["domain"]), name=r["name"], domain=r["domain"])  # noqa: E731
        self.existing_customers = [to_c(r) for r in self.seed_records]  # the seeds are the "customers"
        self.candidates = [to_c(r) for r in self.candidate_records]
        self.default_best = [c.domain for c in self.existing_customers]
        self.data_dir = data_dir

    def record(self, domain: str) -> dict | None:
        domain = registrable_domain(domain)
        for r in self.seed_records + self.candidate_records:
            if r["domain"] == domain:
                return r
        return None

    def by_domain(self, domain: str) -> Company | None:
        reg = registrable_domain(domain)
        for c in self.existing_customers + self.candidates:
            if c.domain == reg:
                return c
        return None

    def by_id(self, cid: str) -> Company | None:
        for c in self.existing_customers + self.candidates:
            if c.id == cid:
                return c
        return None


def real_snapshot_available(data_dir: Path = REAL_DIR) -> bool:
    return (data_dir / "companies.json").exists()

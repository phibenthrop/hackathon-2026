"""Live web research through Tavily (stdlib only), with a hard cap per run.

Every source keeps its URL, title, a short excerpt for display and a longer body that the
quote gate checks against. Web text is data: it is never treated as instructions.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field

from .models_registry import TAVILY_BASE_URL

MAX_TAVILY_CALLS = 5
MAX_MODEL_CALLS = 6  # score (2 on invalid JSON) + plan + write, each may fall back once
MAX_INPUT_CHARS = 200
MAX_SOURCES = 8
BODY_CHARS = 5000
EXCERPT_CHARS = 400
TAVILY_TIMEOUT = 20

DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:/.*)?$", re.IGNORECASE)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    """Caps for one run: Tavily calls and model calls."""
    tavily_max: int = MAX_TAVILY_CALLS
    model_max: int = MAX_MODEL_CALLS
    tavily_used: int = 0
    model_used: int = 0

    def take_tavily(self) -> None:
        if self.tavily_used >= self.tavily_max:
            raise BudgetExceeded(f"Tavily cap reached ({self.tavily_max} calls per run).")
        self.tavily_used += 1

    def take_model(self) -> None:
        if self.model_used >= self.model_max:
            raise BudgetExceeded(f"Model cap reached ({self.model_max} calls per run).")
        self.model_used += 1


@dataclass
class Source:
    n: int
    url: str
    title: str
    excerpt: str
    body: str
    kind: str  # "website" | "search" | "news"
    published: str | None = None

    def public(self) -> dict:
        return {"n": self.n, "url": self.url, "title": self.title, "excerpt": self.excerpt,
                "kind": self.kind, "published": self.published, "chars": len(self.body)}


@dataclass
class Target:
    raw: str
    name: str
    domain: str | None
    by_domain: bool = False  # the user typed a domain (stricter source filter)


@dataclass
class Research:
    target: Target
    sources: list[Source] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # one entry per Tavily call

    @property
    def credits(self) -> int:
        return sum(c.get("credits", 1) for c in self.calls)


def clean_input(s: str | None, limit: int = MAX_INPUT_CHARS) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def parse_target(raw: str) -> Target:
    raw = clean_input(raw)
    m = DOMAIN_RE.match(raw) if " " not in raw else None
    if m:
        domain = m.group(1).lower()
        stem = domain.split(".")[0]
        return Target(raw, stem.replace("-", " ").title(), domain, True)
    return Target(raw, raw, None)


class Tavily:
    def __init__(self, api_key: str, budget: Budget):
        self.api_key = api_key
        self.budget = budget

    def _post(self, path: str, payload: dict) -> tuple[dict, float]:
        self.budget.take_tavily()
        req = urllib.request.Request(TAVILY_BASE_URL + path, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.api_key}"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=TAVILY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data, time.monotonic() - t0

    def search(self, query: str, topic: str = "general", time_range: str | None = None,
               max_results: int = 3) -> tuple[list[dict], float]:
        payload = {"query": query[:300], "search_depth": "basic", "max_results": max_results,
                   "topic": topic, "include_raw_content": "text", "include_answer": False}
        if time_range:
            payload["time_range"] = time_range
        data, dt = self._post("/search", payload)
        return data.get("results") or [], dt

    def extract(self, urls: list[str]) -> tuple[list[dict], float]:
        data, dt = self._post("/extract", {"urls": urls[:5], "extract_depth": "basic", "format": "text"})
        return data.get("results") or [], dt


def _squash(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def depersonalize(text: str) -> str:
    """Drop sentences that name people, e-mails, phones or testimonials (same filter as the snapshot)."""
    from .realdata import SENT_SPLIT, _personal
    return " ".join(x for x in SENT_SPLIT.split(_squash(text)) if not _personal(x))


def _queries(t: Target) -> list[tuple[str, str, str | None, str]]:
    name = f'"{t.name}"'
    site = f" {t.domain}" if t.domain else ""
    return [
        (f"{name}{site} company", "general", None, "search"),
        (f"{name} news", "news", "year", "news"),
        (f"{name} locations depots fleet employees", "general", None, "search"),
    ]


# pages about individual people are never used (no personal data), nor job/social noise
SKIP_URL = re.compile(r"/people/|/team/|/profile/|/staff/|linkedin\.com/in/|facebook\.com|instagram\.com|tiktok\.com|x\.com/|twitter\.com|indeed\.|glassdoor\.|tavily\.com/",
                      re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_+&.]+", " ", s.lower()).strip()


def _host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/:]+)", url or "")
    return m.group(1).lower() if m else ""


def mentions(t: Target, text: str, url: str) -> bool:
    """A search hit counts only if it is on the target's domain or names the target as a whole word.

    With a typed domain, a short name (6 letters or fewer, e.g. "commit") is too common to count on its
    own: then only the domain itself (on the host or in the text) counts."""
    if t.domain:
        host = _host(url)
        if host == t.domain or host.endswith("." + t.domain):
            return True
        if re.search(r"(?<![\w.-])" + re.escape(t.domain) + r"(?![\w-])", text + " " + url, re.I):
            return True
        if t.by_domain and len(re.sub(r"[^a-z0-9]", "", t.name.lower())) <= 6:
            return False
    words = [w for w in re.split(r"[\s\-_+&.]+", t.name.strip()) if w]
    if not words:
        return False
    pat = r"(?<![\w])" + r"[\s\-_+&.]*".join(re.escape(w) for w in words) + r"(?![\w])"
    return bool(re.search(pat, text, re.I))


def official_domain(t: Target, urls: list[str]) -> str | None:
    """The first hit whose host contains the company name without spaces (e.g. nagel-group.com)."""
    want = re.sub(r"[^a-z0-9]", "", t.name.lower())
    for u in urls:
        m = re.match(r"https?://(?:www\.)?([^/]+)", u)
        if m and want and want in re.sub(r"[^a-z0-9]", "", m.group(1).split(".")[0].lower()):
            return m.group(1).lower()
    return None


def run_research(target: Target, api_key: str, budget: Budget, client: Tavily | None = None) -> Research:
    """Company website (extract) plus three targeted searches: at most 4 Tavily calls."""
    # (5 when only a name is given: the official site is found in the first search, then extracted)
    tv = client or Tavily(api_key, budget)
    res = Research(target)
    seen: set[str] = set()

    def add(url: str, title: str, body: str, kind: str, snippet: str | None = None, published=None):
        url = (url or "").strip()
        key = url.rstrip("/").lower()
        body = depersonalize(body)[:BODY_CHARS]
        if not url.startswith(("http://", "https://")) or key in seen or len(body) < 40 or SKIP_URL.search(url):
            return
        if kind != "website" and not mentions(target, body, url):
            return
        if len(res.sources) >= MAX_SOURCES:
            return
        seen.add(key)
        res.sources.append(Source(len(res.sources) + 1, url, _squash(title)[:160] or url,
                                  depersonalize(snippet or "")[:EXCERPT_CHARS] or body[:EXCERPT_CHARS], body, kind, published))

    def extract_site(domain: str) -> None:
        try:
            rows, dt = tv.extract([f"https://{domain}"])
            res.calls.append({"call": "extract", "query": f"https://{domain}", "seconds": round(dt, 2),
                              "results": len(rows), "credits": 1})
            for r in rows:
                add(r.get("url"), f"{domain} (company website)", r.get("raw_content"), "website")
        except BudgetExceeded:
            raise
        except Exception as exc:  # unreachable site: carry on with search
            res.calls.append({"call": "extract", "query": f"https://{domain}", "error": str(exc)[:160],
                              "credits": 1})

    if target.domain:
        extract_site(target.domain)

    for i, (query, topic, time_range, kind) in enumerate(_queries(target)):
        try:
            rows, dt = tv.search(query, topic=topic, time_range=time_range)
        except BudgetExceeded:
            break
        except Exception as exc:
            res.calls.append({"call": "search", "query": query, "error": str(exc)[:160], "credits": 1})
            continue
        res.calls.append({"call": f"search ({topic})", "query": query, "seconds": round(dt, 2),
                          "results": len(rows), "credits": 1})
        for r in rows:
            add(r.get("url"), r.get("title"), r.get("raw_content") or r.get("content"), kind,
                snippet=r.get("content"), published=r.get("published_date"))
        if i == 0 and not target.domain:  # name only: find the official site among the first hits
            site = official_domain(target, [r.get("url") or "" for r in rows])
            if site:
                target.domain = site
                extract_site(site)
    return res


def keys_present() -> bool:
    return bool(os.environ.get("NEBIUS_API_KEY", "").strip() and os.environ.get("TAVILY_API_KEY", "").strip())

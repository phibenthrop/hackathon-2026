"""Evidence helpers: sentences, keyword hits, quote verification, links.

Every quote the app shows must be a verbatim slice of the website text.
``verify_quote`` is the single gate both scorers pass through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote as urlquote, urlencode

from .sources import Website

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+")
NEGATIONS = re.compile(r"\b(not|no|never|without|nor|cannot|don't|doesn't|do not)\b", re.IGNORECASE)
SIZE_PATTERN = re.compile(
    r"(\d{1,3}(?:,\d{3})+|\d+)\s+(?:employees|people|staff|team members|members of staff)\b",
    re.IGNORECASE,
)


@dataclass
class Hit:
    page: str
    sentence: str
    term: str
    negated: bool


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s and s.strip()]


def term_regex(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(term.strip()) + r"(?![\w-])", re.IGNORECASE)


def find_terms(site: Website, terms: list[str]) -> list[Hit]:
    """All sentences that contain one of ``terms``, in page order."""
    patterns = [(t, term_regex(t)) for t in terms if t.strip()]
    hits: list[Hit] = []
    for page, text in site.pages.items():
        for sent in sentences(text):
            for term, pat in patterns:
                if pat.search(sent):
                    hits.append(Hit(page, sent, term, bool(NEGATIONS.search(sent))))
                    break
    return hits


def find_sizes(site: Website) -> list[tuple[str, str, int]]:
    """(page, sentence, number) for every headcount statement."""
    out = []
    for page, text in site.pages.items():
        for sent in sentences(text):
            m = SIZE_PATTERN.search(sent)
            if m:
                out.append((page, sent, int(m.group(1).replace(",", ""))))
    return out


def collapse_ws(s: str) -> str:
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def verify_quote(site: Website, quote: str | None, page: str | None = None) -> str | None:
    """Return the page where ``quote`` occurs verbatim (whitespace-insensitive), else None.

    If ``page`` is given and the quote is there, that page wins; otherwise all pages are searched.
    """
    if not quote or len(quote.strip()) < 8:
        return None
    q = collapse_ws(quote)
    if page and page in site.pages and q in collapse_ws(site.pages[page]):
        return page
    for p, text in site.pages.items():
        if q in collapse_ws(text):
            return p
    return None


def fragment_url(domain: str, page: str, quote: str, base_url: str | None = None) -> str:
    """Public URL with a text fragment (#:~:text=) pointing at the quote.

    ``base_url`` is the page URL as fetched (real data); otherwise https://<domain><page>.
    """
    words = quote.split()
    if len(words) <= 8:
        frag = urlquote(quote, safe="")
    else:
        frag = urlquote(" ".join(words[:4]), safe="") + "," + urlquote(" ".join(words[-4:]), safe="")
    base = (base_url or f"https://{domain}{page}").split("#")[0]
    if base.count("/") == 2:  # bare origin: https://host -> https://host/
        base += "/"
    return f"{base}#:~:text={frag}"


def local_source_link(company_id: str, page: str, quote: str) -> str:
    return f"/source/{company_id}?" + urlencode({"page": page, "q": quote})

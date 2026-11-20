"""Derive an ICP (ideal customer profile) from the websites of the best customers.

Deterministic and explainable: a term becomes part of the ICP when it appears
(not negated) on the websites of at least two of the three best customers.
Size comes from the headcounts the customers state; region from the country
each customer names first. Everything is returned as editable criteria.
"""

from __future__ import annotations

import math

from .evidence import find_sizes, find_terms, term_regex
from .models import Criterion
from .sources import Website

INDUSTRY_POOL = [
    "cold chain", "cold storage", "temperature-controlled", "refrigerated", "frozen",
    "chilled", "deep-freeze", "pharmaceutical", "fresh produce", "dairy",
]

SIGNAL_POOL: list[dict] = [
    {"id": "sig_expansion", "label": "Signal: expanding (new site or capacity)",
     "keywords": ["new warehouse", "new site", "new hub", "new location", "expanding", "expansion",
                  "additional capacity", "opening"]},
    {"id": "sig_compliance", "label": "Signal: audit / compliance pressure",
     "keywords": ["HACCP", "GDP", "IFS", "audit", "audits", "audited", "certified"]},
    {"id": "sig_manual", "label": "Signal: manual temperature records",
     "keywords": ["paper", "spreadsheet", "spreadsheets", "manual", "manually", "by hand", "clipboard"]},
    {"id": "sig_hiring", "label": "Signal: hiring operations staff",
     "keywords": ["we are hiring", "open positions", "vacancies", "join our team", "hiring"]},
    {"id": "sig_sustainability", "label": "Signal: sustainability programme",
     "keywords": ["sustainability", "biofuel", "carbon", "emissions"]},
]

# Place names per country, used for the region criterion. Demo-sized on purpose.
GAZETTEER: dict[str, list[str]] = {
    "Germany": ["Germany", "Hamburg", "Berlin", "Munich", "Bremen", "Leipzig", "Cologne", "Frankfurt",
                "Hanover", "Dortmund", "Nuremberg", "Stuttgart", "Bavaria", "Saxony", "Hesse",
                "Lower Saxony", "Kassel", "Duisburg", "Bonn", "Erfurt", "Thuringia", "Ruhr"],
    "Austria": ["Austria", "Vienna", "Linz", "Graz", "Salzburg", "Klagenfurt", "Styria", "Carinthia"],
    "Switzerland": ["Switzerland", "Swiss", "Zurich", "Basel", "Bern", "Geneva", "Thurgau"],
    "Netherlands": ["Netherlands", "Rotterdam", "Amsterdam"],
    "Belgium": ["Belgium", "Flanders", "Brussels"],
    "France": ["France", "Lyon", "Paris"],
    "Poland": ["Poland", "Lodz", "Poznan", "Warsaw"],
    "Portugal": ["Portugal", "Lisbon"],
    "Spain": ["Spain", "Madrid"],
    "Italy": ["Italy", "Milan"],
    "Denmark": ["Denmark", "Copenhagen"],
    "Czech Republic": ["Czech Republic", "Czechia", "Prague"],
    "United Kingdom": ["United Kingdom", "London"],
}

MIN_SEEDS = 2


def _seed_count(sites: list[Website], term: str) -> list[str]:
    return [s.company.name for s in sites if any(not h.negated for h in find_terms(s, [term]))]


def home_country(site: Website) -> str | None:
    """Country whose place name appears first on the site."""
    best: tuple[int, int, str] | None = None
    for page_idx, text in enumerate(site.pages.values()):
        for country, places in GAZETTEER.items():
            for place in places:
                m = term_regex(place).search(text)
                if m and (best is None or (page_idx, m.start()) < best[:2]):
                    best = (page_idx, m.start(), country)
    return best[2] if best else None


def derive_icp(sites: list[Website]) -> list[Criterion]:
    n = len(sites)
    criteria: list[Criterion] = []

    # Industry
    industry_terms, notes = [], []
    for term in INDUSTRY_POOL:
        found = _seed_count(sites, term)
        if len(found) >= min(MIN_SEEDS, n):
            industry_terms.append(term)
            notes.append(f"'{term}' ({len(found)}/{n})")
    criteria.append(Criterion(
        id="industry", label="Industry: temperature-controlled logistics", group="industry",
        kind="keywords", keywords=industry_terms or INDUSTRY_POOL[:4], weight=3, must_have=True,
        rationale="Terms found on your best customers' sites: " + (", ".join(notes) or "none"),
    ))

    # Size
    counts = []
    for s in sites:
        sizes = find_sizes(s)
        if sizes:
            counts.append((s.company.name, sizes[0][2]))
    if counts:
        lo = min(c for _, c in counts)
        hi = max(c for _, c in counts)
        mn = max(1, math.floor(lo * 0.5 / 10) * 10)
        mx = math.ceil(hi * 2 / 10) * 10
        why = ", ".join(f"{name}: {c}" for name, c in counts)
    else:
        mn, mx, why = 50, 500, "no headcount stated; default range"
    criteria.append(Criterion(
        id="size", label=f"Size: {mn}-{mx} employees", group="size", kind="size",
        min=mn, max=mx, weight=2,
        rationale=f"Headcounts of your best customers ({why}); range = half the smallest to twice the largest.",
    ))

    # Region
    countries: list[str] = []
    for s in sites:
        c = home_country(s)
        if c and c not in countries:
            countries.append(c)
    countries = countries or ["Germany", "Austria", "Switzerland"]
    region_terms = [p for c in countries for p in GAZETTEER[c]]
    criteria.append(Criterion(
        id="region", label=f"Region: {', '.join(countries)}", group="region", kind="region",
        keywords=region_terms, weight=2,
        rationale="Home countries of your best customers: " + ", ".join(countries),
    ))

    # Signals
    for sig in SIGNAL_POOL:
        hits = [t for t in sig["keywords"] if _seed_count(sites, t)]
        found = [s.company.name for s in sites
                 if any(not h.negated for h in find_terms(s, sig["keywords"]))]
        criteria.append(Criterion(
            id=sig["id"], label=sig["label"], group="signal", kind="keywords",
            keywords=sig["keywords"], weight=1, enabled=len(found) >= min(MIN_SEEDS, n),
            rationale=(f"Seen at {len(found)}/{n} best customers"
                       + (f" ({', '.join(found)})" if found else "")
                       + (f"; terms: {', '.join(hits)}" if hits else "")),
        ))
    return criteria

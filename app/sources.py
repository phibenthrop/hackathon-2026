"""Company sources and website readers.

Two small interfaces keep the demo data swappable:

* ``CompanySource`` answers "which companies are similar to these customers?"
  ``DemoCompanySource`` replays a canned answer from ``data/companies.json``
  (fictional); ``realdata.SimilarCompaniesSource`` asks similarcompanies.ai.
* ``WebsiteReader`` returns the text of a company website, page by page.
  ``LocalWebsiteReader`` reads ``data/sites/<id>.txt`` (fictional);
  ``realdata.HttpWebsiteReader`` fetches public pages, respecting robots.txt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAGE_MARKER = re.compile(r"^\[page:\s*(\S+)\s*\]\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    domain: str
    similarity: float | None = None

    @property
    def website(self) -> str:
        return f"https://{self.domain}"


@dataclass
class Website:
    company: Company
    pages: dict[str, str] = field(default_factory=dict)  # path -> text
    urls: dict[str, str] = field(default_factory=dict)  # path -> fetched public URL (real data only)

    def page_url(self, page: str) -> str:
        return self.urls.get(page) or f"https://{self.company.domain}{page}"

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.pages.values())

    def word_count(self) -> int:
        return len(self.full_text.split())


class CompanySource(Protocol):
    def similar(self, seeds: list[Company], limit: int = 50) -> list[Company]: ...


class WebsiteReader(Protocol):
    def read(self, company: Company) -> Website: ...


def parse_site_file(text: str) -> dict[str, str]:
    """Split a site file into pages at ``[page: /path]`` markers."""
    pages: dict[str, str] = {}
    matches = list(PAGE_MARKER.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages[m.group(1)] = text[m.end():end].strip()
    return pages


class DemoDataset:
    """Loads the fictional demo dataset once."""

    def __init__(self, data_dir: Path = DATA_DIR):
        raw = json.loads((data_dir / "companies.json").read_text(encoding="utf-8"))
        self.seller = raw["seller"]
        self.existing_customers = [Company(**c) for c in raw["existing_customers"]]
        self.default_best = list(raw["default_best_customers"])
        self.similar_db = [Company(**c) for c in raw["similar_db"]]
        self.data_dir = data_dir

    def by_domain(self, domain: str) -> Company | None:
        domain = normalize_domain(domain)
        for c in self.existing_customers + self.similar_db:
            if c.domain == domain:
                return c
        return None

    def by_id(self, company_id: str) -> Company | None:
        for c in self.existing_customers + self.similar_db:
            if c.id == company_id:
                return c
        return None


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.removeprefix("www.")
    return value.split("/")[0]


class DemoCompanySource:
    """Stands in for similarcompanies.ai: returns a canned 'similar companies' answer."""

    name = "demo database (fictional companies)"

    def __init__(self, dataset: DemoDataset):
        self.dataset = dataset

    def similar(self, seeds: list[Company], limit: int = 50) -> list[Company]:
        seed_domains = {s.domain for s in seeds}
        ranked = sorted(self.dataset.similar_db, key=lambda c: -(c.similarity or 0))
        return [c for c in ranked if c.domain not in seed_domains][:limit]


class LocalWebsiteReader:
    """Reads cached website text from ``data/sites/<id>.txt``."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.sites_dir = data_dir / "sites"
        self._cache: dict[str, Website] = {}

    def read(self, company: Company) -> Website:
        if company.id not in self._cache:
            path = self.sites_dir / f"{company.id}.txt"
            pages = parse_site_file(path.read_text(encoding="utf-8")) if path.exists() else {}
            self._cache[company.id] = Website(company=company, pages=pages)
        return self._cache[company.id]


def remove_existing(candidates: list[Company], existing: list[Company]) -> tuple[list[Company], list[Company]]:
    """Split candidates into (new prospects, already customers), matched by domain."""
    existing_domains = {c.domain for c in existing}
    keep = [c for c in candidates if c.domain not in existing_domains]
    removed = [c for c in candidates if c.domain in existing_domains]
    return keep, removed

"""Real dataset (similarcompanies.ai snapshot): runs offline, no token, no network."""

import json
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.realdata import (
    REAL_DIR, MCPError, RealDataset, HttpWebsiteReader, SimilarCompaniesSource, brand_stem,
    filter_and_merge, html_to_text, pick_extra_pages, registrable_domain,
)

pytestmark = pytest.mark.skipif(not (REAL_DIR / "companies.json").exists(), reason="no real snapshot")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "SIMILARCOMPANIES_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    def no_network(*a, **kw):
        raise AssertionError("network access in snapshot mode")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def real_run(client):
    r = client.post("/api/qualify", json={"dataset": "real"})
    assert r.status_code == 200, r.text
    return r.json()


def test_real_is_default_and_named(client):
    cfg = client.get("/api/config").json()
    assert cfg["dataset"] == "real" and cfg["default_dataset"] == "real"
    assert {d["key"] for d in cfg["datasets"]} == {"real", "fictional"}
    assert "similarcompanies.ai, snapshot 20" in cfg["source_line"]
    assert "public company websites" in cfg["source_line"]
    assert client.get("/api/config?dataset=fictional").json()["dataset"] == "fictional"


def test_real_quotes_are_verbatim_and_link_to_the_real_page(real_run):
    ds, reader = RealDataset(), HttpWebsiteReader(live=False)
    n = 0
    for r in real_run["results"]:
        site = reader.read(ds.by_id(r["id"]))
        for f in r["findings"]:
            if f["status"] == "no_evidence":
                assert f["quote"] is None
                continue
            assert f["quote"] in site.pages[f["page"]], (r["id"], f["quote"])
            base = site.urls[f["page"]]
            base += "/" if base.count("/") == 2 else ""
            assert f["url"].startswith(base + "#:~:text="), f["url"]
            assert f["url"].startswith("http") and ".example" not in f["url"]
            n += 1
    assert n > 60


def test_real_seeds_not_in_list_and_domains_unique(real_run):
    ds = RealDataset()
    seed_regs = {registrable_domain(s["domain"]) for s in ds.seed_records}
    seed_stems = {brand_stem(s["domain"]) for s in ds.seed_records}
    domains = [r["domain"] for r in real_run["results"]]
    assert 30 <= len(domains) <= 40
    regs = [registrable_domain(d) for d in domains]
    assert len(set(regs)) == len(regs)
    assert not set(regs) & seed_regs
    assert not {brand_stem(d) for d in domains} & seed_stems


def test_real_has_no_size_criterion_and_no_similarity(client, real_run):
    crit = client.post("/api/icp", json={"dataset": "real"}).json()["criteria"]
    assert "size" not in {c["id"] for c in crit}
    assert next(c for c in crit if c["id"] == "industry")["must_have"]
    assert all(r["similarity"] is None for r in real_run["results"])
    assert set(real_run["tier_counts"]) == {"A", "B", "C"}


def test_real_source_page_and_drafts_only(client, real_run):
    r = next(x for x in real_run["results"] if any(f["quote"] for f in x["findings"]))
    f = next(f for f in r["findings"] if f["quote"])
    page = client.get(f["source_link"])
    assert page.status_code == 200 and "<mark" in page.text
    for x in real_run["results"]:
        assert x["draft_hook"] is None or x["draft_hook"].startswith("DRAFT - review before sending")


def test_snapshot_rejects_other_seeds(client):
    r = client.post("/api/candidates", json={"dataset": "real", "best_customers": ["example.com"]})
    assert r.status_code == 400


def test_no_secret_in_snapshot():
    for p in [REAL_DIR / "companies.json", *sorted((REAL_DIR / "sites").glob("*.json"))]:
        text = p.read_text(encoding="utf-8")
        assert "accessToken" not in text and "Bearer" not in text, p


def test_snapshot_pages_have_no_contact_or_team_paths_and_no_emails():
    for p in sorted((REAL_DIR / "sites").glob("*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        for pg in snap["pages"]:
            assert not any(w in pg["path"].lower() for w in ("contact", "impressum", "imprint", "team", "kontakt"))
            assert "@" not in pg["text"], (p.name, pg["path"])


def test_filter_drops_seed_brands_and_merges_duplicates():
    seeds = [{"domain": "acme.co.uk", "name": "Acme"}]
    raw = [
        {"name": "Acme", "domain": "acme.fr", "website": "https://www.acme.fr/"},
        {"name": "Acme", "domain": "help.acme.co.uk", "website": "https://help.acme.co.uk/"},
        {"name": "Other Ltd", "domain": "other.com", "website": "https://www.other.com/"},
        {"name": "Other Ltd", "domain": "www.other.com", "website": "https://other.com"},
        {"name": "Third", "domain": "third.co.uk", "website": "https://third.co.uk"},
    ]
    kept, stats = filter_and_merge(raw, seeds)
    assert [k["domain"] for k in kept] == ["other.com", "third.co.uk"]
    assert stats["seed_brand"] == 2 and stats["duplicate"] == 1


def test_more_without_search_id_is_refused():
    src = SimilarCompaniesSource(token="dummy", live=True)
    with pytest.raises(MCPError):
        src._find({"more": True})
    assert src.calls_used == 0


def test_html_to_text_drops_people_and_picks_safe_pages():
    html = """<html lang="en"><nav><a href="/about-us/">About</a><a href="/contact">Contact</a>
      <a href="/our-team">Team</a><a href="/services/chilled">Chilled</a><a href="https://other.com/about">x</a></nav>
      <main><p>We run temperature-controlled trucks across the UK.</p>
      <p>Call us on 01234 567890 or write to info@acme.co.uk today.</p>
      <p>The company was founded by Jane Doe in 1990.</p><p>Since 1985 it has been run by Mark Smith.</p>
      <p>Menu</p></main><footer>Copyright Acme</footer></html>"""
    text, links, lang = html_to_text(html)
    assert text == "We run temperature-controlled trucks across the UK."
    picked = pick_extra_pages("https://acme.co.uk/", links)
    assert picked == ["https://acme.co.uk/about-us/", "https://acme.co.uk/services/chilled"]

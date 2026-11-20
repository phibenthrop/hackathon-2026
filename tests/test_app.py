import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import LLMConfig
from app.scoring import LLMScorer, score_company
from app.sources import DemoDataset, LocalWebsiteReader


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "SIMILARCOMPANIES_TOKEN"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def run(client):
    r = client.post("/api/qualify", json={"dataset": "fictional"})
    assert r.status_code == 200
    return r.json()


def test_dataset_is_fictional_and_sized():
    ds = DemoDataset()
    reader = LocalWebsiteReader()
    assert len(ds.similar_db) == 25
    for c in ds.similar_db + ds.existing_customers:
        assert c.domain.endswith(".example"), c.domain
        words = reader.read(c).word_count()
        assert 150 <= words <= 300, (c.id, words)


def test_every_quote_is_verbatim_in_website_text(run):
    reader = LocalWebsiteReader()
    ds = DemoDataset()
    n_quotes = 0
    for r in run["results"]:
        site = reader.read(ds.by_id(r["id"]))
        for f in r["findings"]:
            if f["status"] == "no_evidence":
                assert f["quote"] is None
                continue
            assert f["quote"], f"verdict without quote: {r['id']} {f['criterion_id']}"
            assert f["quote"] in site.pages[f["page"]], (r["id"], f["quote"])
            assert f["url"].startswith(f"https://{r['domain']}{f['page']}#:~:text=")
            n_quotes += 1
    assert n_quotes > 50


def test_existing_customers_are_excluded(run):
    existing = {c.domain for c in DemoDataset().existing_customers}
    result_domains = {r["domain"] for r in run["results"]}
    assert not existing & result_domains
    removed = {c["domain"] for c in run["removed_existing_customers"]}
    assert removed == {"velmora-kuehl.example", "tiefgrund.example"}
    assert len(run["results"]) == 23


def test_tier_distribution_is_not_uniform(run):
    counts = run["tier_counts"]
    assert set(counts) == {"A", "B", "C"}
    assert all(v >= 3 for v in counts.values()), counts


def test_negated_mention_does_not_count(run):
    vossbeck = next(r for r in run["results"] if r["id"] == "vossbeck")
    industry = next(f for f in vossbeck["findings"] if f["criterion_id"] == "industry")
    assert industry["status"] == "not_met"
    assert vossbeck["tier"] == "C"


def test_disabling_criteria_changes_result(client):
    crit = client.post("/api/icp", json={"dataset": "fictional"}).json()["criteria"]
    for c in crit:
        c["enabled"] = c["id"] == "industry"
    r = client.post("/api/qualify", json={"dataset": "fictional", "criteria": crit}).json()
    assert all(len(x["findings"]) == 1 for x in r["results"])


def test_pages_and_csv(client, run):
    home = client.get("/")
    assert home.status_code == 200 and "Agent drafts, a person decides." in home.text
    assert "Next steps" in client.get("/slides").text
    csv_resp = client.get(f"/api/export/{run['run_id']}.csv?tiers=A")
    assert csv_resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert rows and all(row["tier"] == "A" for row in rows)
    src = client.get("/source/quellbeck", params={"page": "/about", "q": "Today we have 140 employees"})
    assert "<mark" in src.text


def test_llm_quotes_are_checked_and_fabrications_discarded(monkeypatch):
    ds, reader = DemoDataset(), LocalWebsiteReader()
    site = reader.read(ds.by_id("quellbeck"))
    crit = main.derive_icp([reader.read(ds.by_domain(d)) for d in ds.default_best])
    fake = {"findings": [
        {"criterion_id": "industry", "status": "met", "reason": "cold store",
         "quote": "We store chilled and frozen goods in our warehouse in Dortmund", "page": "/"},
        {"criterion_id": "size", "status": "met", "reason": "invented",
         "quote": "Quellbeck employs 900 people worldwide.", "page": "/about"},
    ]}
    scorer = LLMScorer(LLMConfig(base_url="http://unused.invalid", model="fake", api_key="x"))
    monkeypatch.setattr(scorer.client, "chat", lambda messages, temperature=0.0: json.dumps(fake))
    res = score_company(site, crit, scorer)
    by = {f.criterion_id: f for f in res.findings}
    assert by["industry"].status == "met" and by["industry"].quote in site.pages["/"]
    assert by["size"].status == "no_evidence" and by["size"].quote is None
    assert "discarded" in by["size"].reason
    assert by["region"].status == "no_evidence"  # model gave no verdict -> no evidence


def test_rules_used_without_key(client):
    assert client.get("/api/config").json()["llm"] is False


def test_script_page_is_served_unlinked_and_noindex(client):
    r = client.get("/script")
    assert r.status_code == 200 and 'name="robots" content="noindex"' in r.text
    for page in ("/", "/live", "/slides"):
        assert '"/script"' not in client.get(page).text  # "</script>" tags contain the bare path

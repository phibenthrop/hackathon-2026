"""LLM scorer: fit verdict, replay of precomputed verdicts, scorer switch. No network."""

import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.evidence import collapse_ws
from app.llm import extract_json
from app.realdata import REAL_DIR, HttpWebsiteReader, RealDataset

ROOT = Path(__file__).resolve().parent.parent
needs_real = pytest.mark.skipif(not (REAL_DIR / "companies.json").exists(), reason="no real snapshot")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "SIMILARCOMPANIES_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    def no_network(*a, **kw):
        raise AssertionError("network access in replay mode")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def replay_file(tmp_path, monkeypatch):
    """A small precomputed file: one verified non-fit, one fabricated non-fit, one candidate."""
    doc = {
        "model": "test/model", "provider": "Nebius Token Factory", "computed": "2026-11-20",
        "criteria": ["industry", "sector", "region", "sig_expansion"],
        "companies": {
            "tcsandd.com": {
                "fit": {"verdict": "publisher", "reason": "Trade magazine.",
                        "quote": "The magazine’s title, spells the editorial policy", "page": "/about/"},
                "findings": [{"criterion_id": "industry", "status": "met", "reason": "x",
                              "quote": "Temperature Controlled Storage and Distribution (TCS&D)", "page": "/about/"}],
            },
            "thermo-king.co.uk": {
                "fit": {"verdict": "manufacturer", "reason": "invented", "quote": "We build 900 trucks a day.",
                        "page": "/"},
                "findings": [],
            },
            "arcticcouriers.co.uk": {
                "fit": {"verdict": "candidate", "reason": "Chilled courier.",
                        "quote": "Same Day Temperature Controlled Delivery", "page": "/"},
                "findings": [{"criterion_id": "industry", "status": "met", "reason": "x",
                              "quote": "Same Day Temperature Controlled Delivery", "page": "/"},
                             {"criterion_id": "region", "status": "met", "reason": "invented",
                              "quote": "Arctic Couriers is the largest operator in Wales.", "page": "/"}],
            },
        },
    }
    p = tmp_path / "llm_scores.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(main, "LLM_SCORES", p)
    return p


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure: {"a": 2} done') == {"a": 2}
    assert extract_json("no json") is None and extract_json(None) is None


@needs_real
def test_replay_without_key_is_default_for_real_and_labelled(client, replay_file):
    cfg = client.get("/api/config?dataset=real").json()
    assert cfg["scorer_mode"] == "precomputed" and cfg["llm"] is False and cfg["llm_available"] is True
    assert cfg["scorer_line"] == "LLM verdicts: test/model via Nebius Token Factory, precomputed 2026-11-20"
    assert client.get("/api/config?dataset=fictional").json()["scorer_mode"] == "rules"
    rules = client.get("/api/config?dataset=real&scorer=rules").json()
    assert rules["scorer_mode"] == "rules"


@needs_real
def test_replay_gate_fit_and_tier(client, replay_file):
    run = client.post("/api/qualify", json={"dataset": "real"}).json()
    assert run["scorer_mode"] == "precomputed"
    by = {r["domain"]: r for r in run["results"]}
    tcs = by["tcsandd.com"]
    assert tcs["not_a_fit"] and tcs["tier"] == "C" and tcs["fit"]["verdict"] == "publisher"
    assert tcs["summary"].startswith("Not a fit (publisher)")
    tk = by["thermo-king.co.uk"]  # fabricated quote: claim ignored, stays a candidate
    assert tk["not_a_fit"] is False and tk["fit"]["discarded"] and tk["fit"]["quote"] is None
    ac = {f["criterion_id"]: f for f in by["arcticcouriers.co.uk"]["findings"]}
    assert ac["industry"]["status"] == "met"
    assert ac["region"]["status"] == "no_evidence" and "discarded" in ac["region"]["reason"]
    # companies missing from the file get no verdict, never an invented one
    other = next(r for r in run["results"] if r["domain"] not in replay_file.read_text())
    assert all(f["status"] == "no_evidence" for f in other["findings"])
    assert run["not_a_fit"] == 1


@needs_real
def test_rules_switch_still_works(client, replay_file):
    run = client.post("/api/qualify", json={"dataset": "real", "scorer": "rules"}).json()
    assert run["scorer_mode"] == "rules" and run["not_a_fit"] == 0
    assert client.post("/api/qualify", json={"dataset": "real", "scorer": "bogus"}).status_code == 400


@needs_real
@pytest.mark.skipif(not (REAL_DIR / "llm_scores.json").exists(), reason="no precomputed LLM file")
def test_stored_llm_file_quotes_verbatim_after_gate(client):
    doc = json.loads((REAL_DIR / "llm_scores.json").read_text(encoding="utf-8"))
    assert doc["model"] and doc["computed"] and doc["provider"]
    run = client.post("/api/qualify", json={"dataset": "real"}).json()
    assert run["scorer_mode"] == "precomputed"
    ds, reader = RealDataset(), HttpWebsiteReader(live=False)
    for r in run["results"]:
        site = reader.read(ds.by_id(r["id"]))
        quotes = [(f["quote"], f["page"]) for f in r["findings"] if f["quote"]]
        if r["fit"] and r["fit"]["quote"]:
            quotes.append((r["fit"]["quote"], r["fit"]["page"]))
        for q, page in quotes:
            assert collapse_ws(q) in collapse_ws(site.pages[page]), (r["domain"], q)


def test_no_api_key_in_tracked_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    pat = re.compile(r"v1\.Cm|Bearer [A-Za-z0-9._-]{20,}|sk-[A-Za-z0-9]{20,}")
    for f in files + ["data/real/llm_scores.json"]:
        p = ROOT / f
        if p.is_file() and p.suffix in {".py", ".json", ".md", ".html", ".js", ".toml", ".txt", ".example", ""}:
            assert not pat.search(p.read_text(encoding="utf-8", errors="ignore")), f

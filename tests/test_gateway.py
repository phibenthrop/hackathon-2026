"""Branch otari: the gateway switch is off by default and, when on, routes the same Nebius model."""
import json

from app import gateway, outreach
from app.llm import LLMClient


class _Resp:
    def __init__(self, data, headers=None):
        self._b = json.dumps(data).encode()
        self.headers = headers or {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(seen):
    def urlopen(req, timeout=None):
        seen.append((req.full_url, json.loads(req.data)["model"], req.headers.get("Authorization")))
        return _Resp({"choices": [{"message": {"content": "{}"}}],
                      "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": "0.000123"}})
    return urlopen


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY", raising=False)
    assert not gateway.active() and gateway.info()["active"] is False


def test_on_routes_nebius_model_through_otari(monkeypatch):
    seen = []
    monkeypatch.setenv("LLM_GATEWAY", "otari")
    monkeypatch.setenv("OTARI_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.setenv("OTARI_API_KEY", "tk-dummy")
    monkeypatch.setattr("app.llm.urllib.request.urlopen", _fake(seen))
    out = outreach.nebius_chat("zai-org/GLM-5.3-Flash", [{"role": "user", "content": "x"}], 100, 5,
                               outreach.Budget())
    assert seen == [("http://127.0.0.1:9/api/v1/chat/completions", "nebius:zai-org/GLM-5.3-Flash", "Bearer tk-dummy")]
    assert out["gateway_cost_usd"] == 0.000123
    assert gateway.info()["name"] == "Mozilla Otari"


def test_off_goes_to_nebius(monkeypatch):
    seen = []
    monkeypatch.delenv("LLM_GATEWAY", raising=False)
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy")
    monkeypatch.setattr("app.llm.urllib.request.urlopen", _fake(seen))
    outreach.nebius_chat("zai-org/GLM-5.3-Flash", [{"role": "user", "content": "x"}], 100, 5, outreach.Budget())
    assert seen[0][0].startswith("https://api.tokenfactory.nebius.com/v1") and seen[0][1] == "zai-org/GLM-5.3-Flash"


def _fake_full(seen, headers):
    def urlopen(req, timeout=None):
        seen.append({"url": req.full_url, "body": json.loads(req.data),
                     "headers": {k.lower(): v for k, v in req.header_items()}})
        return _Resp({"choices": [{"message": {"content": "{}"}}],
                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, headers)
    return urlopen


def _on(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY", "otari")
    monkeypatch.setenv("OTARI_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.setenv("OTARI_API_KEY", "tk-dummy")


def test_step_sends_policy_user_and_reads_otari_fallback(monkeypatch):
    _on(monkeypatch)
    seen = []
    monkeypatch.setattr("app.llm.urllib.request.urlopen", _fake_full(seen, {
        "Otari-Model": "moonshotai/Kimi-K3", "Otari-Fallback": "true", "Otari-Attempt-Count": "2"}))
    from app.models_registry import steps
    b = outreach.Budget()
    b.ctx = {"user": "demo-a", "outage": True}
    out = outreach.step_chat(steps()["write"], [{"role": "user", "content": "x"}], b, guard=True)
    sent = seen[0]
    assert sent["body"]["model"] == "icp-write-outage" and sent["body"]["user"] == "demo-a"
    assert sent["body"]["guardrails"][0]["profile"] == gateway.GUARD_PROFILE
    assert sent["headers"]["otari-end-user-budget"] == gateway.TRIAL_BUDGET
    assert out["model"] == "moonshotai/Kimi-K3" and out["fallback_from"] == gateway.OUTAGE_MODEL
    assert out["otari"]["attempts"] == 2


def test_unknown_user_falls_back_to_default():
    assert gateway.clean_user("root") == gateway.DEFAULT_USER
    assert gateway.clean_user("demo-a") == "demo-a"


def test_guard_check_flags_contact_data_only():
    assert gateway.guard_check("Call +49 170 5550123")["valid"] is False
    assert gateway.guard_check("mail sales@example.com")["valid"] is False
    assert gateway.guard_check("427 branches in 43 countries since 1930")["valid"] is True


def test_otari_routes_are_404_when_off(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv("LLM_GATEWAY", raising=False)
    c = TestClient(app)
    for method, path in (("get", "/api/otari/status"), ("post", "/api/otari/probe"), ("post", "/api/otari/guard-test"),
                         ("post", "/otari-guardrails/validate"), ("get", "/otari-script")):
        kw = {"json": {"profile": "no-contact-data", "input_text": "x"}} if method == "post" else {}
        assert getattr(c, method)(path, **kw).status_code == 404, path


def test_guardrail_service_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    _on(monkeypatch)
    r = TestClient(app).post("/otari-guardrails/validate", json={"profile": "no-contact-data", "input_text": "x@y.de"})
    assert r.status_code == 200 and r.json()["result"]["valid"] is False


def test_config_has_policies_budgets_and_no_secrets():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("oc", pathlib.Path(__file__).parents[1] / "scripts/otari_config.py")
    oc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oc)
    y = oc.render(1.0, "http://127.0.0.1:8781")
    for s in ("icp-score:", "icp-plan:", "icp-write:", "icp-write-outage:", "on_failure:", "user-trial:",
              "max_budget: 1.0", "search_tools:", "web_fetch_enabled: true", "guardrails_url:"):
        assert s in y, s
    assert "${OTARI_API_KEY}" in y and "${NEBIUS_API_KEY}" in y and ("tvly" + "-") not in y

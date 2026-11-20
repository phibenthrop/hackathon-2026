"""Branch otari: the gateway switch is off by default and, when on, routes the same Nebius model."""
import json

from app import gateway, outreach
from app.llm import LLMClient


class _Resp:
    def __init__(self, data):
        self._b = json.dumps(data).encode()

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

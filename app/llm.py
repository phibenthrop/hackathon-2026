"""Minimal OpenAI-compatible chat client (stdlib only).

Configuration comes only from environment variables:
  LLM_BASE_URL  e.g. https://api.tokenfactory.nebius.com/v1/
  LLM_MODEL     model name at that provider
  LLM_API_KEY   secret; if empty, the app uses the rule-based scorer
  LLM_TIMEOUT_SECONDS (optional, default 45)
No other module knows a URL or a model name.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float = 45.0

    @classmethod
    def from_env(cls) -> "LLMConfig | None":
        key = os.environ.get("LLM_API_KEY", "").strip()
        base = os.environ.get("LLM_BASE_URL", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        if not (key and base and model):
            return None
        return cls(base_url=base, model=model, api_key=key,
                   timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "45")))


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        })
        with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]

"""Minimal OpenAI-compatible chat client (stdlib only).

Configuration comes only from environment variables:
  LLM_BASE_URL  e.g. https://api.tokenfactory.nebius.com/v1/
  LLM_MODEL     model name at that provider
  LLM_API_KEY   secret; if empty, the app uses the rule-based scorer
  LLM_TIMEOUT_SECONDS (optional, default 45)
  LLM_MAX_TOKENS      (optional, default 2500; caps output incl. reasoning tokens)
No other module knows a URL or a model name.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

RETRY_STATUS = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3  # per call; hard cap, no open loops


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float = 45.0
    max_tokens: int = 2500
    max_retries: int = MAX_RETRIES  # the live agent uses 1: a demo cannot wait 4 x timeout
    reasoning_effort: str | None = None  # Nebius "reasoning_effort" (none/low/medium/high); None = model default

    @classmethod
    def from_env(cls) -> "LLMConfig | None":
        key = os.environ.get("LLM_API_KEY", "").strip()
        base = os.environ.get("LLM_BASE_URL", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        if not (key and base and model):
            return None
        return cls(base_url=base, model=model, api_key=key,
                   timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "45")),
                   max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "2500")))


def extract_json(text: str | None) -> dict | None:
    """Parse a JSON object from a model reply; tolerates ```json fences and leading prose."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            out = json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            return None
    return out if isinstance(out, dict) else None


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    errors: int = 0
    retries: int = 0
    gateway_cost_usd: float | None = None  # set when a gateway (Otari) meters the call
    log: list = field(default_factory=list)


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.usage = Usage()

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.config.max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        body = json.dumps(payload).encode()
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            })
            try:
                self.usage.calls += 1
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    data = json.loads(resp.read().decode())
                u = data.get("usage") or {}
                self.usage.prompt_tokens += int(u.get("prompt_tokens") or 0)
                self.usage.completion_tokens += int(u.get("completion_tokens") or 0)
                d = u.get("completion_tokens_details") or {}
                self.usage.reasoning_tokens += int(d.get("reasoning_tokens") or 0)
                if u.get("cost_usd") is not None:
                    self.usage.gateway_cost_usd = (self.usage.gateway_cost_usd or 0) + float(u["cost_usd"])
                msg = data["choices"][0]["message"]
                return msg.get("content") or ""
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in RETRY_STATUS or attempt == self.config.max_retries:
                    self.usage.errors += 1
                    hint = " (credit balance exhausted: add funds)" if exc.code == 402 else ""
                    raise RuntimeError(f"HTTP {exc.code} from LLM endpoint{hint}") from None
                wait = float(exc.headers.get("Retry-After") or 0) or (2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                if attempt == self.config.max_retries:
                    self.usage.errors += 1
                    raise RuntimeError(f"LLM endpoint unreachable: {type(exc).__name__}") from None
                wait = 2 ** attempt
            self.usage.retries += 1
            time.sleep(min(wait, 20) + random.random())
        raise RuntimeError(f"LLM call failed: {last_exc}")

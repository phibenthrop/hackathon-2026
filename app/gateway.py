"""Optional Mozilla Otari gateway (branch `otari`). Off unless LLM_GATEWAY=otari.

On: every model call goes to the self-hosted Otari gateway (OpenAI-compatible,
OTARI_BASE_URL, default http://127.0.0.1:8000/api/v1) with the app key OTARI_API_KEY,
and the model is addressed as ``nebius:<model>``: Otari forwards to Nebius Token Factory,
so the models (and the Nebius rule of the main challenge) stay the same. What Otari adds:
the gateway's own metered cost per call (``usage.cost_usd``) and a hard USD ceiling on the key.
"""

from __future__ import annotations

import os


def active() -> bool:
    return os.environ.get("LLM_GATEWAY", "").strip().lower() == "otari"


def base_url() -> str:
    return os.environ.get("OTARI_BASE_URL", "").strip() or "http://127.0.0.1:8000/api/v1"


def api_key() -> str:
    return os.environ.get("OTARI_API_KEY", "").strip()


def model_name(model: str) -> str:
    return f"nebius:{model}"


def info() -> dict:
    if not active():
        return {"name": None, "active": False}
    return {"name": "Mozilla Otari", "active": True, "base_url": base_url(), "key_set": bool(api_key()),
            "provider": "nebius (Token Factory, behind the gateway)",
            "features": ["gateway-metered cost per call (usage.cost_usd)", "USD budget ceiling on the app key"]}

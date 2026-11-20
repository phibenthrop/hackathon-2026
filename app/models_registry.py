"""Which model does which step of the live agent, and why. The single place to change it.

Every model runs on Nebius Token Factory (OpenAI-compatible). Override a step with an
environment variable, e.g. ``LIVE_MODEL_SCORE=zai-org/GLM-5.3-Flash``.

Prices: USD per 1M tokens (input / output) from the public Nebius catalog
https://tokenfactory.nebius.com/api/public/models_info, read on 2026-11-20.
Tavily: one basic search or one basic extract (up to 5 URLs) costs 1 credit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
TAVILY_BASE_URL = "https://api.tavily.com"
TAVILY_USD_PER_CREDIT = 0.008  # Tavily pay-as-you-go price per credit

# model id -> (input USD / 1M, output USD / 1M, catalog tokens per second)
PRICES: dict[str, tuple[float, float, float]] = {
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24, 314),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24, 60),
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30, 70),
    "zai-org/GLM-5.3-Flash": (0.15, 0.50, 349),
    "openai/gpt-oss-120b": (0.15, 0.60, 40),
    "deepseek-ai/DeepSeek-V4.1-Flash-1017": (0.30, 1.20, 738),
    "MiniMaxAI/MiniMax-M3": (0.30, 1.20, 248),
    "Qwen/Qwen3.8-27B": (0.45, 3.00, 218),
    "deepseek-ai/DeepSeek-V4-Pro-0813": (1.32, 3.96, 37),
    "zai-org/GLM-5.3": (1.40, 4.40, 455),
    "moonshotai/Kimi-K3.1-Pro": (2.00, 10.00, 195),
    "moonshotai/Kimi-K3": (3.00, 15.00, 264.55),
}


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    model: str
    why: str
    max_tokens: int
    timeout: float


_DEFAULTS = [
    ("score", "Score against the ICP", "zai-org/GLM-5.3-Flash",
     "Small, fast, cheap: one call per company, JSON out, and every quote is checked by code anyway, "
     "so we pay for speed, not brilliance.", 1800, 40),
    ("plan", "Plan the outreach", "moonshotai/Kimi-K3",
     "Strong reasoning model, one call per run: choosing whom to address and which hook to use is the "
     "step where judgement matters and the volume is low.", 2500, 60),
    ("write", "Write the email draft", "zai-org/GLM-5.3",
     "Fluent multilingual writer with a natural voice (German and English tested), fast enough for a "
     "live demo, cheaper than the planner.", 1500, 50),
]


def steps() -> dict[str, Step]:
    out = {}
    for key, title, model, why, max_tokens, timeout in _DEFAULTS:
        model = os.environ.get(f"LIVE_MODEL_{key.upper()}", "").strip() or model
        out[key] = Step(key, title, model, why, max_tokens, timeout)
    return out


RESEARCH_WHY = ("Tavily search and extract: the company's own site plus targeted searches "
                "(what they run, recent news, size and sites). Every source keeps its URL.")


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    p = PRICES.get(model)
    if not p:
        return None
    return round((prompt_tokens * p[0] + completion_tokens * p[1]) / 1_000_000, 6)


def registry_json() -> list[dict]:
    rows = [{"step": "research", "title": "Research the company", "model": "Tavily (search + extract)",
             "why": RESEARCH_WHY, "price": f"1 credit per call, about ${TAVILY_USD_PER_CREDIT} each"}]
    for s in steps().values():
        p = PRICES.get(s.model)
        rows.append({"step": s.key, "title": s.title, "model": s.model, "why": s.why,
                     "price": f"${p[0]} / ${p[1]} per 1M tokens in/out" if p else "price unknown"})
    return rows

"""Write a self-hosted Otari config.yml for this app (no secrets: they stay ${ENV} references).

Usage: uv run python scripts/otari_config.py PATH [--budget-usd 1.0]
Prices come from app/models_registry.PRICES, so the gateway meters every Nebius model we use.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.models_registry import NEBIUS_BASE_URL, PRICES  # noqa: E402


def render(budget_usd: float) -> str:
    q = json.dumps  # JSON strings are valid YAML scalars
    lines = ['database_url: "sqlite:///./otari.db"', 'host: "127.0.0.1"', "port: 8000",
             "default_pricing: false", "require_pricing: true", "update_check: false", "",
             "providers:", "  nebius:", "    api_key: ${NEBIUS_API_KEY}", f"    api_base: {q(NEBIUS_BASE_URL)}",
             "    max_retries: 0", "", "pricing:"]
    for model, (pin, pout, _tps) in PRICES.items():
        lines += [f"  {q('nebius:' + model)}:", f"    input_price_per_million: {pin}",
                  f"    output_price_per_million: {pout}"]
    lines += ["", "budgets:", f"  icp-app-ceiling: {{max_budget: {budget_usd}, reset_alignment: calendar_day}}",
              "", "api_keys:", "  icp-qualifier:", "    secret: ${OTARI_API_KEY}", "    user_id: icp-qualifier",
              "    ceiling: icp-app-ceiling", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--budget-usd", type=float, default=1.0)
    a = ap.parse_args()
    pathlib.Path(a.path).write_text(render(a.budget_usd))
    print(f"wrote {a.path}")

"""Write a self-hosted Otari config.yml for this app (no secrets: they stay ${ENV} references).

Usage: uv run python scripts/otari_config.py PATH [--budget-usd 1.0] [--app-url http://127.0.0.1:8781]

What the config switches on (each one is used by the app, see README "Otari variant"):
  * routing policies icp-score / icp-plan / icp-write: primary model + on_failure fallback, run by Otari;
    icp-write-outage: the primary is a model id Nebius does not serve, the fallback is Write's own
    fallback (Kimi-K3): a real fallback on demand (button "Trigger fallback")
  * budgets: a 1 USD/day ceiling on the app's service key, and per end user (demo-a, demo-b) a budget of its own
  * search_tools.tavily: the research step's web search runs through POST /api/v1/search
  * web_fetch_enabled: the company website is read through POST /api/v1/fetch (builtin_fetch)
  * guardrails_url: the app's own small guardrail service (contact data check), used on the write step
Prices come from app/models_registry.PRICES, so the gateway meters every Nebius model we use.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.gateway import OUTAGE_MODEL, TRIAL_BUDGET, STANDARD_BUDGET, USER_BUDGETS, policy_name  # noqa: E402
from app.models_registry import FALLBACKS, NEBIUS_BASE_URL, PRICES, steps  # noqa: E402


def render(budget_usd: float, app_url: str) -> str:
    q = json.dumps  # JSON strings are valid YAML scalars
    lines = ['database_url: "sqlite:///./otari-v2.db"', 'host: "127.0.0.1"', "port: 8000",
             "master_key: ${OTARI_MASTER_KEY}",
             "default_pricing: false", "require_pricing: true", "update_check: false",
             "web_fetch_enabled: true", f"guardrails_url: {q(app_url.rstrip('/') + '/otari-guardrails')}", "",
             "providers:", "  nebius:", "    api_key: ${NEBIUS_API_KEY}", f"    api_base: {q(NEBIUS_BASE_URL)}",
             "    max_retries: 0",
             "    client_args: {timeout: 30}   # a hanging model counts as failed after 30 s: the policy falls back",
             "", "pricing:"]
    priced = dict(PRICES)
    priced[OUTAGE_MODEL] = PRICES["zai-org/GLM-5.3"]  # require_pricing: the fake primary needs a price too
    for model, (pin, pout, _tps) in priced.items():
        lines += [f"  {q('nebius:' + model)}:", f"    input_price_per_million: {pin}",
                  f"    output_price_per_million: {pout}"]
    lines += ["", "routing:", "  enabled: true", "  policies:"]
    for st in steps().values():
        fb = FALLBACKS[st.key].partition("@")[0]
        lines += [f"    {policy_name(st.key)}:", "      select:", f"        - default: {q('nebius:' + st.model)}",
                  "      on_failure:", f"        - {q('nebius:' + fb)}"]
    lines += [f"    {policy_name('write', outage=True)}:", "      select:",
              f"        - default: {q('nebius:' + OUTAGE_MODEL)}",
              "      on_failure:", f"        - {q('nebius:' + FALLBACKS['write'].partition('@')[0])}"]
    lines += ["", "search_tools:", "  tavily:", "    provider: tavily", "    api_key: ${TAVILY_API_KEY}",
              "    options: {search_depth: basic}", "    price_per_call: 0.008   # USD, Tavily pay-as-you-go credit",
              "web_search_default_tool: tavily", "",
              "budgets:", f"  icp-app-ceiling: {{max_budget: {budget_usd}, reset_alignment: calendar_day}}"]
    for bid, usd in ((STANDARD_BUDGET, 0.60), (TRIAL_BUDGET, 0.03)):
        lines.append(f"  {bid}: {{max_budget: {usd}, reset_alignment: calendar_day}}")
    lines += ["", "api_keys:", "  icp-service:", "    secret: ${OTARI_API_KEY}", "    user_id: icp-qualifier",
              "    is_service_key: true", f"    end_user_budget_ids: [{STANDARD_BUDGET}, {TRIAL_BUDGET}]",
              f"    end_user_budget_id: {STANDARD_BUDGET}", "    max_end_users: 10",
              "    ceiling: icp-app-ceiling",
              "    scopes: [inference:run, web:search, web:fetch, usage:read, end-users:read, end-users:write]", ""]
    assert set(USER_BUDGETS.values()) <= {STANDARD_BUDGET, TRIAL_BUDGET}
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--budget-usd", type=float, default=1.0)
    ap.add_argument("--app-url", default="http://127.0.0.1:8781")
    a = ap.parse_args()
    pathlib.Path(a.path).write_text(render(a.budget_usd, a.app_url))
    print(f"wrote {a.path}")

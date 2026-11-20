"""Record one real live run as data/live_example.json (played back when keys are missing).

    set -a; . ~/.config/jarvis/hackday.env; set +a
    uv run python scripts/record_live_example.py chillspeed.co.uk

Runs in-process (no server), costs one live run: 4 Tavily calls, 3 to 4 model calls.
The file holds one company, no person names (people pages and sentences are filtered).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import main, outreach  # noqa: E402


def main_() -> int:
    company = sys.argv[1] if len(sys.argv) > 1 else "chillspeed.co.uk"
    c = TestClient(main.app)
    r = c.post("/api/live/start", json={"company": company})
    if r.status_code != 200:
        print("start failed:", r.status_code, r.json().get("detail"))
        return 1
    rid = r.json()["run_id"]
    for step in ("score", "plan", "write"):
        s = c.post(f"/api/live/{rid}/{step}")
        if s.status_code != 200:
            print(step, "failed:", s.status_code, s.json().get("detail"))
            return 1
        print(step, "ok", s.json()["seconds"], "s")
    rec = c.get(f"/api/live/{rid}/record").json()
    rec["recorded_at"] = dt.datetime.now().isoformat(timespec="minutes")
    outreach.EXAMPLE_FILE.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    print("wrote", outreach.EXAMPLE_FILE, rec.get("totals"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())

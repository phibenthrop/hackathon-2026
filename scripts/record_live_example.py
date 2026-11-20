"""Record one real live run as data/live_example.json (played back when keys are missing).

    set -a; . ~/.config/jarvis/hackday.env; set +a
    uv run python scripts/record_live_example.py chillspeed.co.uk
    uv run python scripts/record_live_example.py dachser.de --profile-from data/live_example.json --out /tmp/x.json

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
    args = sys.argv[1:]
    opt = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or not args[i - 1].startswith("--"))]
    company = pos[0] if pos else "chillspeed.co.uk"
    body = {"company": company}
    if "--profile-from" in opt:  # reuse product and ICP lines of an earlier record
        inp = json.loads(Path(opt["--profile-from"]).read_text())["input"]
        body.update(product=inp["product"], icp=inp["icp"])
    out = Path(opt.get("--out", outreach.EXAMPLE_FILE))
    c = TestClient(main.app)
    r = c.post("/api/live/start", json=body)
    if r.status_code != 200:
        print("start failed:", r.status_code, r.json().get("detail"))
        return 1
    rid = r.json()["run_id"]
    for step in ("score", "plan", "write"):
        s = c.post(f"/api/live/{rid}/{step}", json={"force": True} if step == "write" else None)
        if s.status_code != 200:
            print(step, "failed:", s.status_code, s.json().get("detail"))
            return 1
        print(step, "ok", s.json()["seconds"], "s")
    rec = c.get(f"/api/live/{rid}/record").json()
    rec["recorded_at"] = dt.datetime.now().isoformat(timespec="minutes")
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    print("wrote", out, rec.get("totals"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())

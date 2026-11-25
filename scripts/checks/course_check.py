"""E14: the course layout check. Free, no model call. Lays out random topic graphs and prints how many rules held.

    engine/.venv/bin/python scripts/checks/course_check.py [--cases 500]

Rules: no topic before something it needs, nothing past the date unless `over` lists it, no day over the pace, reviews follow REVIEW_DAYS.
The spoken-request side (ten scripted tool calls) is `tests/engine/test_teach_course.py::test_ten_spoken_requests_save_what_was_asked`.
The paid pick check is `toolpick.py --runs 1 --only course_make`.
"""
import argparse
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "src"))
from misty_engine.teach import course as tc  # noqa: E402
from misty_engine.teach import map as tm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=500)
    a = ap.parse_args()
    rnd, bad, placed = random.Random(14), 0, 0
    for _ in range(a.cases):
        n = rnd.randint(0, 60)
        tops = [{"topic_id": f"t{i}", "needs": [f"t{j}" for j in rnd.sample(range(i), min(i, rnd.randint(0, 3)))]} for i in range(n)]
        today = date(2026, 11, 1)
        by = (today + timedelta(days=rnd.randint(0, 90))).isoformat()
        lay = tc.layout(tops, {}, today.isoformat(), by, rnd.choice([None, 1, 2, 0.5]))
        day = {t: x["day"] for x in lay["days"] for t in x["new"]}
        placed += len(day)
        ok = set(day) | set(lay["over"]) == {t["topic_id"] for t in tops}
        ok &= all(day[nd] < day[t["topic_id"]] for t in tops if t["topic_id"] in day for nd in t["needs"] if nd in day)
        ok &= all(x["day"] <= by and len(x["new"]) <= max(1, math.ceil(lay["per_day"])) and len(x["review"]) <= tc.MAX_REVIEWS_PER_DAY for x in lay["days"])
        for t, dd in day.items():
            got = sorted(x["day"] for x in lay["days"] if t in x["review"])
            ok &= all(date.fromisoformat(r) >= date.fromisoformat(dd) + timedelta(days=tm.REVIEW_DAYS[k]) for k, r in enumerate(got))
        bad += not ok
    print(f"course layout: {a.cases - bad} of {a.cases} random courses kept every rule ({placed} topics placed), $0.00")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

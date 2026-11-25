#!/usr/bin/env python3
"""F7 scored check: is the "was that for me" judge good enough to answer without her name? PAID (one small model call per phrase),
stops at --max-usd. About $0.02 to $0.04 for 150 labelled phrases on two routes.

    engine/.venv/bin/python scripts/checks/addressed_eval.py --labels ~/Misty/checks/addressed.yaml --max-usd 0.05
    engine/.venv/bin/python scripts/checks/addressed_eval.py --labels ~/Misty/checks/addressed.yaml --dry      # free: file and prompt only

Needs F6's labels first: `~/Misty/checks/addressed.yaml` (written by scripts/label_phrases.py), at least 150 phrases and 40 of each kind.
The file is split in two halves by a hash of the text (so the split never moves). The FIRST half is for tuning: both routes (Haiku 5.5 and
the Jev route) judge it, and the cheaper one that reaches the bar wins (else the more precise). The SECOND half is held out: only the chosen
route judges it, once, and that is the score.

The bar, on the held-out half:
  precision >= 97%   of the phrases she would answer, how many really were for her
  recall    >= 85%   of the phrases meant for her, how many she answers
If both are met the script writes ~/Misty/checks/addressed.pass and the `ambient` setting may then be `judge`. If not, it deletes any
old pass file: this card ships nothing and strict (name only) mode stays. That is a fine result. If the money stops first, the rest is
"paid, pending" and no pass file is written.

Note: the labelled file does not keep her last reply (the collector keeps no more than the time since it), so the eval judges each phrase
with less context than the live judge gets. It can only be harder here than live, not easier.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402,F401  (puts engine/src on the path)

HOME = Path.home() / "Misty"
LABELS = ("for_her", "not_for_her", "write")
NEED_TOTAL, NEED_EACH = 150, 40          # F6's bar for the labelled set


# ---- the labels and the split ------------------------------------------------------------------------------------------
def load(path) -> list[dict]:
    import yaml
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"no labels at {p}: run scripts/label_phrases.py first (F6)")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise SystemExit(f"{p} is not valid YAML: {e}")
    if not isinstance(data, list):
        raise SystemExit(f"{p} must be a list of phrases")
    rows = []
    for i, r in enumerate(data):
        if not isinstance(r, dict) or not str(r.get("text") or "").strip() or r.get("label") not in LABELS:
            raise SystemExit(f"{p} row {i + 1} needs text and a label of for_her, write or not_for_her")
        rows.append(r)
    return rows


def side(text: str) -> int:
    """0 = tuning half, 1 = held-out half. A hash of the words, so it never changes when rows are added or reordered."""
    key = " ".join(str(text).lower().split())
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % 2


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    return [r for r in rows if side(r["text"]) == 0], [r for r in rows if side(r["text"]) == 1]


# ---- the scoring -------------------------------------------------------------------------------------------------------
ACTS = ("for_her", "write")


def _right(lab, said) -> bool:
    """`lab` is one label or a list of labels that are all fine (an instruction to write may be done either way)."""
    return said in (lab if isinstance(lab, (list, tuple)) else (lab,))


def _meant(lab) -> bool:
    return any(x in ACTS for x in (lab if isinstance(lab, (list, tuple)) else (lab,)))


def score(results) -> dict:
    """results: (true label or labels, what the judge said, cost). `for_her` and `write` are answers; the wrong one is a wrong answer."""
    n = len(results)
    answered = sum(1 for _, said, _ in results if said in ACTS)
    right = sum(1 for lab, said, _ in results if said in ACTS and _right(lab, said))
    meant = sum(1 for lab, _, _ in results if _meant(lab))
    cost = sum(c for _, _, c in results)
    return {"n": n, "answered": answered, "right": right, "wrong_answers": answered - right, "meant": meant,
            "precision": (right / answered) if answered else None, "recall": (right / meant) if meant else None,
            "cost": cost, "cost_per_phrase": (cost / n) if n else 0.0}


def meets_bar(s: dict) -> bool:
    from misty_engine import addressed
    return s.get("precision") is not None and s["precision"] >= addressed.BAR_PRECISION and (s.get("recall") or 0.0) >= addressed.BAR_RECALL


def _fmt(s: dict) -> str:
    p = "n/a" if s["precision"] is None else f"{s['precision']:.1%}"
    r = "n/a" if s["recall"] is None else f"{s['recall']:.1%}"
    return (f"precision {p} ({s['right']} of {s['answered']} answered)  recall {r} ({s['right']} of {s['meant']} meant for her)  "
            f"wrong answers {s['wrong_answers']}  ${s['cost_per_phrase']:.5f} a phrase")


# ---- the run -----------------------------------------------------------------------------------------------------------
async def _judge_all(judge, rows, budget, put) -> tuple[list, float, int]:
    """(results, dollars, how many were not asked because the money ran out)."""
    out, spent = [], 0.0
    for i, r in enumerate(rows):
        if spent >= budget:
            return out, spent, len(rows) - i
        since = r.get("since_reply")
        room = r.get("room")
        res = await judge.judge(str(r["text"]), since_reply=since if isinstance(since, (int, float)) else None, named=bool(r.get("named")),
                                app=str(r.get("app") or ""), room=room if isinstance(room, int) else None, typing=bool(r.get("typing")))
        usd = float(res.get("usd") or 0.0)
        spent += usd
        lab = [r["label"], *r["also"]] if isinstance(r.get("also"), list) else r["label"]
        out.append((lab, res.get("label", "unsure"), usd))
    return out, spent, 0


async def evaluate(rows: list[dict], judges: dict, *, max_usd: float, pass_file: Path, put=print) -> dict:
    """Tune on the first half, score the second once. Returns {passed, route, tune, held, pending, spent}."""
    from misty_engine import addressed
    tune, held = split(rows)
    spent, pending, tuned = 0.0, 0, {}
    for route, j in judges.items():
        res, usd, left = await _judge_all(j, tune, max_usd - spent, put)
        spent += usd
        pending += left
        if res:
            tuned[route] = score(res)
            put(f"tune  {route:6} {_fmt(tuned[route])}")
    out = {"passed": False, "route": None, "tune": tuned, "held": None, "pending": pending, "spent": spent}
    if not tuned:
        _drop(pass_file)
        return out
    good = [r for r, s in tuned.items() if meets_bar(s)]
    route = (min(good, key=lambda r: tuned[r]["cost_per_phrase"]) if good
             else max(tuned, key=lambda r: ((tuned[r]["precision"] or 0.0), (tuned[r]["recall"] or 0.0))))
    out["route"] = route
    put(f"chosen on the tuning half: {route}")
    res, usd, left = await _judge_all(judges[route], held, max_usd - spent, put)
    spent += usd
    out["spent"], out["pending"] = spent, pending + left
    s = score(res)
    out["held"] = s
    put(f"held   {route:6} {_fmt(s)}")
    n_her = sum(1 for r in held if r["label"] in ACTS)
    ok = (out["pending"] == 0 and meets_bar(s) and len(held) >= addressed.MIN_HELD
          and n_her >= addressed.MIN_EACH and len(held) - n_her >= addressed.MIN_EACH)
    out["passed"] = ok
    if ok:
        addressed.write_pass(pass_file, route=route, precision=s["precision"], recall=s["recall"], held=len(held),
                             for_her=n_her, not_for_her=len(held) - n_her)
    else:
        _drop(pass_file)                                     # a run that fails takes the old pass away
    return out


def _drop(pass_file: Path) -> None:
    try:
        Path(pass_file).unlink()
    except FileNotFoundError:
        pass


class RealJudge:
    def __init__(self, ctx, route):
        self.ctx, self.route = ctx, route

    async def judge(self, text, **kw):
        from misty_engine import addressed
        return await addressed.judge(self.ctx, text, route=self.route, force=True, **kw)


def counts(rows) -> tuple[int, int]:
    her = sum(1 for r in rows if r["label"] in ACTS)
    return her, len(rows) - her


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(HOME / "checks" / "addressed.yaml"))
    ap.add_argument("--max-usd", type=float, default=0.05)
    ap.add_argument("--routes", default="haiku,jev")
    ap.add_argument("--pass-file", default=str(HOME / "checks" / "addressed.pass"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    rows = load(a.labels)
    her, nope = counts(rows)
    print(f"{len(rows)} labelled phrases: {her} for her, {nope} not.")
    if len(rows) < NEED_TOTAL or her < NEED_EACH or nope < NEED_EACH:
        print(f"Not enough to score: the bar needs {NEED_TOTAL} labelled, at least {NEED_EACH} of each kind. Label more with scripts/label_phrases.py.")
        return 2
    tune, held = split(rows)
    th, tn = counts(tune)
    hh, hn = counts(held)
    print(f"tuning half {len(tune)} ({th} for her, {tn} not); held-out half {len(held)} ({hh} for her, {hn} not).")
    if a.dry:
        from misty_engine import addressed
        sample = addressed.prompt(str(rows[0]["text"]), since_reply=rows[0].get("since_reply"), named=bool(rows[0].get("named")))
        print(f"dry run: the file is fine, the prompt builds ({len(addressed.RULES) + len(sample)} characters), no model was called, no pass file touched.")
        return 0
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        ctx.settings.set("cap_daily_usd", 5.0)
        routes = [r.strip() for r in a.routes.split(",") if r.strip()]
        judges = {r: RealJudge(ctx, r) for r in routes}
        out = asyncio.run(evaluate(rows, judges, max_usd=a.max_usd, pass_file=Path(a.pass_file).expanduser()))
    finally:
        _harness.cleanup(home)
    print(f"\ntotal ${out['spent']:.4f} (limit ${a.max_usd:.2f})")
    if out["pending"]:
        print(f"paid, pending: {out['pending']} phrases were not asked (the money ran out). No pass file was written.")
    if out["passed"]:
        print(f"PASS: route {out['route']}. Wrote {a.pass_file}. The ambient setting may now be `judge`.")
        return 0
    print("NOT PASSED: precision under 97% or recall under 85% on the held-out half (or too little to tell). Nothing ships; strict (name only) mode stays.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

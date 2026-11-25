"""D6 scored check (PAID, about $0.05 to $0.08, a throwaway db): 10 spoken-style descriptions with "yes, save it" in the same
message. Pass = the saved steps are exactly the expected moves, in order, in at least 9 of 10.
`--dry` checks the fixture against the move registry for free (every expected move exists)."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

import yaml  # noqa: E402

FIXTURE = _harness.ROOT / "tests" / "engine" / "fixtures" / "protocol_build.yaml"


def load_all():
    rows, facts_ = [], []
    raw = yaml.safe_load(FIXTURE.read_text())
    if isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict) and "say" in r]
        for r in raw:
            if isinstance(r, dict) and "facts" in r:
                facts_ = r["facts"]
    return rows, facts_


async def main(dry: bool) -> int:
    from misty_engine import chat, facts, moves, protocols
    rows, fx = load_all()
    known = set(moves.MOVES) | {"open_fact"}
    bad = [m for r in rows for m in r["expect"] if m not in known]
    if bad:
        print("unknown moves in the fixture:", sorted(set(bad)))
        return 2
    if dry:
        print(f"fixture ok: {len(rows)} descriptions, all expected moves exist")
        return 0
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        for f in fx:
            facts.remember(ctx, f["key"], f["text"], f.get("link"), f.get("link_title"))

        async def st(s): ...
        async def yes(q): return True
        ok = 0
        for i, r in enumerate(rows):
            with ctx.con:
                ctx.con.execute("DELETE FROM chat_messages")
                ctx.con.execute("UPDATE protocols SET status='dropped'")
            async def nop(s): ...
            await chat.send(ctx, r["say"] + ". Yes, save it.", on_delta=nop, on_status=st, confirm=yes)
            rows_ = protocols.active(ctx.con)
            got = [s["move"] for s in protocols.steps_of(rows_[0])] if rows_ else []
            good = got == r["expect"]
            ok += good
            print(("ok  " if good else "MISS"), r["say"][:70], "->", got)
        print(f"protocol build: {ok} of {len(rows)} (bar 9)")
        return 0 if ok >= 9 else 1
    finally:
        _harness.cleanup(home)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().dry)))

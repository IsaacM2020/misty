"""D3 scored check (free, no model): 20 scripted runs of the test-only move `test_write` with mixed answers.
Pass = 0 rows written without a yes, and every yes written exactly once. Prints `N of 20`."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

from misty_engine import moves  # noqa: E402
from misty_engine.moves import registry, testing  # noqa: E402

ANSWERS = ["yes", "no", "timeout", "yes", "no", "yes", "timeout", "no", "yes", "no",
           "no", "yes", "timeout", "yes", "no", "no", "yes", "timeout", "no", "yes"]


async def main() -> int:
    ctx, home = _harness.temp_ctx(copy_live=False)
    registry.CONFIRM_SECONDS = 0.05
    testing.register_test_write()
    try:
        for i, a in enumerate(ANSWERS):
            async def confirm(q, a=a):
                if a == "timeout":
                    await asyncio.sleep(1)
                return a == "yes"
            await moves.run(ctx, "test_write", {"what": f"run{i}"}, f"chk{i}", confirm)
        wrote = set(testing.rows(ctx))
        want = {f"run{i}" for i, a in enumerate(ANSWERS) if a == "yes"}
        without_yes = len(wrote - want)
        ok = sum(1 for i, a in enumerate(ANSWERS) if (f"run{i}" in wrote) == (a == "yes"))
        print(f"moves ask: {ok} of {len(ANSWERS)} right; rows written without a yes: {without_yes}")
        return 0 if ok == len(ANSWERS) and without_yes == 0 else 1
    finally:
        _harness.cleanup(home)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

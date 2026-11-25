"""D10 scored check (free, no model): breaking moves and protocols on purpose.

Fault 1 is real: a child process runs a 5 step protocol, gets SIGKILLed while step 3 is under way, and a new process resumes
the same run. Pass = every step's effect happened exactly once. Faults 2 to 6 are tests/engine/test_moves_faults.py (fakes
where a real fault is unsafe); this script runs that file and prints the card's scores.

Usage: uv run python ../scripts/checks/moves_faults.py   (from engine/)"""
import asyncio
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


# ---------------------------------------------------------------- fault 1, the child side

async def child(mode: str, home: str, run_id: str) -> None:
    sys.path.insert(0, str(HERE))
    import _harness
    from misty_engine import protocols
    from misty_engine.moves.base import Move, Outcome, Param
    from misty_engine.moves.registry import register
    effects = Path(home) / "effects.txt"

    async def step(ctx, p):
        if mode == "hang" and p["n"] == 3:
            await asyncio.sleep(3600)                 # the kill comes here, between step 2 and step 3's effect
        with effects.open("a") as f:
            f.write(f"{p['n']}\n")
        return Outcome(True, what=f"step {p['n']}")

    async def proved(ctx, p, o):
        return True
    register(Move("fault_step", "fault test only", {"n": Param(int, lo=1, hi=9)}, "safe", step, proved,
                  says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    ctx, _ = _harness.temp_ctx(copy_live=False, home=Path(home))
    if protocols.get(ctx.con, "five") is None:
        protocols.save(ctx, "five", ["five steps"], [{"move": "fault_step", "params": {"n": n}} for n in range(1, 6)])

    async def quiet(_s): ...
    r = await protocols.run(ctx, protocols.get(ctx.con, "five"), None, quiet, run_id=run_id)
    print("RESULT", r["ok"], len(r["done"]))


def fault1() -> tuple[bool, str]:
    home = tempfile.mkdtemp(prefix="misty-fault1-")
    effects = Path(home) / "effects.txt"
    me = [sys.executable, __file__, "--child"]
    p = subprocess.Popen(me + ["hang", home, "runK"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 60:
        if effects.exists() and len(effects.read_text().split()) >= 2:
            break
        if p.poll() is not None:
            return False, "the child ended before the kill: " + p.stdout.read().decode()[-300:]
        time.sleep(0.05)
    time.sleep(0.5)                                   # step 2 logged, step 3 hanging
    p.send_signal(signal.SIGKILL)
    p.wait()
    killed_at = effects.read_text().split() if effects.exists() else []
    r = subprocess.run(me + ["resume", home, "runK"], capture_output=True, text=True, timeout=120)
    got = effects.read_text().split() if effects.exists() else []
    ok = got == ["1", "2", "3", "4", "5"] and killed_at == ["1", "2"] and "RESULT True 5" in r.stdout
    shutil.rmtree(home, ignore_errors=True)
    return ok, f"effects before kill {killed_at}, after resume {got}" + ("" if ok else f"; child said {r.stdout[-200:]}{r.stderr[-300:]}")


# ---------------------------------------------------------------- the rest, through pytest

def faults_2_to_6() -> dict:
    out = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests/engine/test_moves_faults.py"), "-q", "-rA", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, cwd=ROOT / "engine")
    res = {}
    for line in out.stdout.splitlines():
        m = re.match(r"^(PASSED|FAILED|ERROR) \S+::(test_\w+)(?:\[(.*)\])?", line)
        if m:
            res[(m.group(2), m.group(3) or "")] = m.group(1) == "PASSED"
    return res


def main() -> int:
    ok1, how = fault1()
    print(f"fault 1, cut mid-protocol (real SIGKILL): {'PASS' if ok1 else 'FAIL'} ({how})")
    res = faults_2_to_6()
    if not res:
        print("could not run tests/engine/test_moves_faults.py")
        return 1
    good = ok1
    for n, prefix in ((2, "test_fault2"), (3, "test_fault3"), (4, "test_fault4")):
        rows = [v for (t, _), v in res.items() if t.startswith(prefix)]
        print(f"fault {n}: {'PASS' if rows and all(rows) else 'FAIL'} ({sum(rows)} of {len(rows)} cases)")
        good = good and bool(rows) and all(rows)
    inj = [(case, v) for (t, case), v in res.items() if t == "test_fault5_injected_orders"]
    print(f"injected orders: {sum(v for _, v in inj)} of {len(inj)}" + "".join(f"\n  FAIL {c}" for c, v in inj if not v))
    var = [(case, v) for (t, case), v in res.items() if t == "test_fault6_varied_starts"]
    print(f"varied starts: {sum(v for _, v in var)} of {len(var)} verified" + "".join(f"\n  FAIL {c}" for c, v in var if not v))
    print("note: the real calendar_add (fault 2) waits on D4; a stand-in move was used")
    good = good and len(inj) == 10 and all(v for _, v in inj) and len(var) == 20 and all(v for _, v in var)
    return 0 if good else 1


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--child":
        asyncio.run(child(sys.argv[2], sys.argv[3], sys.argv[4]))
    else:
        sys.exit(main())

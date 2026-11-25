"""D7 scored check (free, local embedder only): the match set from tests/engine/fixtures/protocol_match.yaml, run through
`protocols.match` with the real embedder. Pass = 20 of 20 matched and 0 of 20 near-misses matched.
`--calibrate` also prints, for the embedding tier alone, the best similarity of each line (to pick EMBED_THRESHOLD)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

import yaml  # noqa: E402

FIX = _harness.ROOT / "tests" / "engine" / "fixtures" / "protocol_match.yaml"


def main(calibrate: bool) -> int:
    from misty_engine import facts, protocols
    spec = yaml.safe_load(FIX.read_text())
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        for p in spec["protocols"]:
            protocols.save(ctx, p["key"], p["triggers"], [{"move": "open_app", "params": {"name": "Notes"}}], None)
        hit = sum(1 for s, want in spec["matches"] if (r := protocols.match(ctx, s)) is not None and r["key"] == want)
        false = sum(1 for s in spec["misses"] if protocols.match(ctx, s) is not None)
        print(f"protocol match: {hit} of {len(spec['matches'])} matched, {false} of {len(spec['misses'])} near-misses matched")
        if calibrate:
            rows = protocols.active(ctx.con)
            trig = [t for r in rows for t in protocols.triggers_of(r)]
            tv = {t: facts._embed(ctx, t) for t in trig}
            def best(s):
                q = facts._embed(ctx, " ".join(protocols._words(s)))
                return max(float(q @ v) for v in tv.values())
            print("matches, best similarity:", sorted(round(best(s), 3) for s, _ in spec["matches"]))
            print("misses, best similarity: ", sorted(round(best(s), 3) for s in spec["misses"]))
        return 0 if hit == len(spec["matches"]) and false == 0 else 1
    finally:
        _harness.cleanup(home)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    sys.exit(main(ap.parse_args().calibrate))

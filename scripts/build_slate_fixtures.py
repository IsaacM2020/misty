"""Build app/Misty/Resources/slate/fixtures.js (checked slates for dev.html) from tests/engine/fixtures/slates/good."""
import json, pathlib
from misty_engine import slate

root = pathlib.Path(__file__).resolve().parent.parent
out = {}
for p in sorted((root / "tests/engine/fixtures/slates/good").glob("*.json")):
    out[p.stem] = slate.build(json.loads(p.read_text()))
dest = root / "app/Misty/Resources/slate/fixtures.js"
dest.write_text("window.FIXTURES = " + json.dumps(out, ensure_ascii=False) + ";\n")
print(len(out), "fixtures ->", dest)

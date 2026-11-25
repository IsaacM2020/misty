#!/bin/bash
# The regression wall (SPEC section 4) as one command. Free checks only: prints one PASS or FAIL line per row, then
# lists the paid checks with the command to run each (they cost a few cents, the exam needs Isaac's yes first).
# Usage: scripts/checks/wall.sh [--quick]    (--quick skips the two full test suites)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
FAILS=0
row() {  # row "name" "detail" status
  if [ "$3" = 0 ]; then echo "PASS  $1  $2"; else echo "FAIL  $1  $2"; FAILS=$((FAILS+1)); fi
}
last() { tail -n 1 | tr -d '\r'; }

if [ "${1:-}" != "--quick" ]; then
  out=$(cd engine && uv run pytest ../tests/engine -q 2>&1); st=$?
  row "engine tests" "$(echo "$out" | last)" $st
  out=$(bash scripts/swift_test.sh 2>&1); st=$?
  n=$(echo "$out" | grep "Executed" | tail -n 1 | sed 's/^[[:space:]]*//')
  if echo "$out" | grep -q "with [1-9][0-9]* failures"; then st=1; fi
  row "swift tests" "$n" $st
fi

one() {  # one "row name" test-file
  out=$(cd engine && uv run pytest "../tests/engine/$2" -q 2>&1); st=$?
  row "$1" "$(echo "$out" | last)" $st
}
one "gate cases" test_gate.py
one "no second cloud path" test_no_bypass.py
one "exam prompt frozen" test_exam_frozen.py
if [ -f tests/engine/test_no_person.py ]; then
  one "no person in code" test_no_person.py
else
  echo "SKIP  no person in code  (guard test arrives in card B4)"
fi
out=$(python3 scripts/checks/firstword.py 2>&1); st=$?
row "first word" "$out" $st

# not a pass or fail, just a number to look at (card B2b)
if [ -f engine/.venv/bin/misty ]; then
  n=$(sqlite3 -readonly "$HOME/Misty/misty.db" "SELECT COUNT(*) FROM wrong_picks WHERE status='new'" 2>/dev/null || echo "n/a")
  echo "INFO  wrong picks waiting: $n"
fi

echo
echo "Paid checks (not run here, a few cents each):"
echo "  tool pick   : engine/.venv/bin/python scripts/checks/toolpick.py"
echo "  anybody     : engine/.venv/bin/python scripts/checks/anybody.py --live"
echo "  memory exam : cd engine && ./.venv/bin/misty exam --via chat   (ask Isaac first)"
echo
if [ "$FAILS" = 0 ]; then echo "WALL: all PASS"; else echo "WALL: $FAILS FAIL"; fi
exit $FAILS

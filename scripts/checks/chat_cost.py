#!/usr/bin/env python3
"""Chat cost per user message, from the real db. Read only.

    engine/.venv/bin/python scripts/checks/chat_cost.py [--days 14] [--db ~/Misty/misty.db] [--since-ts N]

Prints, for assistant replies that carry meta.rounds: messages, rounds per message, tokens in / cached / out per message,
how many first rounds missed the cache, and usd per message (from the rounds, which hold the provider's own cost)."""
import argparse, json, sqlite3, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=float, default=14)
ap.add_argument("--since-ts", type=float, default=None)
ap.add_argument("--db", default=str(Path.home() / "Misty" / "misty.db"))
a = ap.parse_args()
con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
since = a.since_ts if a.since_ts is not None else time.time() - a.days * 86400
cols = [r[1] for r in con.execute("PRAGMA table_info(chat_messages)")]
tcol = "ts" if "ts" in cols else ("created_at" if "created_at" in cols else cols[0])
rows = con.execute(f"SELECT meta FROM chat_messages WHERE role='assistant' AND meta LIKE '%rounds%' AND {tcol}>=?", (since,)).fetchall()
via = {}
n = rounds = tin = tc = tout = miss = 0
usd = 0.0
for (m,) in rows:
    try:
        rs = json.loads(m).get("rounds") or []
    except Exception:
        continue
    if not rs:
        continue
    n += 1
    rounds += len(rs)
    miss += 1 if rs[0].get("cached", 0) == 0 else 0
    for r in rs:
        via[r.get('via') or '?'] = via.get(r.get('via') or '?', 0) + 1
        tin += r.get("in", 0); tc += r.get("cached", 0); tout += r.get("out", 0); usd += r.get("usd", 0)
w = con.execute("SELECT COUNT(*), COALESCE(SUM(usd),0) FROM spend WHERE purpose='chat' AND ts>=?", (since,)).fetchone()
if not n:
    print("no replies with rounds in range"); raise SystemExit
print(f"messages {n}  rounds/msg {rounds/n:.2f}  first-round cache miss {miss}/{n}")
print(f"per message: in {tin/n:.0f}  cached {tc/n:.0f}  out {tout/n:.0f}  usd {usd/n:.5f}")
print(f"spend table, purpose=chat: {w[0]} calls ${w[1]:.3f} (includes warms and anything without rounds)")
print("answered by (rounds saved since the provider field was added):", via)

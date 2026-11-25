"""Product principle P45: Misty has no Claude subscription path. Not for Isaac, not for anyone."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "engine" / "src" / "misty_engine"
BAD = [r"claude_runner", r"ClaudeRunner", r"claude_cli", r"ClaudeCliCfg", r"QuotaHit", r"waiting_quota_release",
       r"release_due_quota", r"\bclaude -p\b",
       r"create_subprocess_exec\([^)]*['\"]claude['\"]",
       r"\bctx\.claude\b", r"\bsubprocess\b[^\n]*['\"]claude['\"]"]


def test_no_subscription_paths():
    hits = []
    for p in SRC.rglob("*.py"):
        t = p.read_text()
        for pat in BAD:
            if re.search(pat, t):
                hits.append(f"{p.relative_to(SRC)}: {pat}")
    assert hits == []


def test_the_claude_mem_reader_is_still_there_and_is_not_a_runner():
    # sources/claude_mem.py reads a local SQLite database. It must not be mistaken for the subscription runner.
    t = (SRC / "sources" / "claude_mem.py").read_text()
    assert "subprocess" not in t and "claude -p" not in t

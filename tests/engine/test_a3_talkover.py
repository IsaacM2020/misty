"""F4: the a3 report reads the talk-over lines of the voice log (numbers only)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a3_report_counts_talk_overs_and_lines_that_ended(tmp_path):
    log = tmp_path / "voice.log"
    log.write_text("\n".join([
        "2026-11-17T07:00:00Z UPDATE_LINE mic open",
        "2026-11-17T07:00:04Z UPDATE_LINE end talked_over=false",
        "2026-11-17T07:01:00Z UPDATE_LINE mic open",
        "2026-11-17T07:01:02Z VOICE_TURN barge_stop_ms=120 source=update out_ms=60",
        "2026-11-17T07:02:00Z VOICE_TURN barge_stop_ms=350 source=update out_ms=60",
        "2026-11-17T07:03:00Z VOICE_TURN barge_stop_ms=90",
    ]) + "\n")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "a3_report.py"), "--log", str(log)], capture_output=True, text=True).stdout
    assert "talk over her own lines" in out
    assert "1 of 2 within 300 ms" in out
    assert "lines that ended on their own: 1; stopped by talking over: 2" in out

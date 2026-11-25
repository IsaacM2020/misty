import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from misty_engine import keychain
from misty_engine.api import build_methods
from misty_engine.errors import Fatal

ENGINE_SRC = Path(__file__).resolve().parents[2] / "engine" / "src"


def env_for(home):
    e = dict(os.environ)
    e["MISTY_HOME"] = str(home)
    e["PYTHONPATH"] = str(ENGINE_SRC)
    e["MISTY_NO_WARM"] = "1"  # no 1.4 GB embedder download per test engine
    return e


def wait_for(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


def test_engine_exits_when_parent_dies(short_tmp):
    home = short_tmp / "m"
    sock = short_tmp / "e.sock"
    code = ("import subprocess,sys,os,time; "
            "subprocess.Popen([sys.executable,'-m','misty_engine','serve','--socket',sys.argv[1],"
            "'--parent-pid',str(os.getpid())]); time.sleep(120)")
    parent = subprocess.Popen([sys.executable, "-c", code, str(sock)], env=env_for(home))
    try:
        assert wait_for(sock.exists), "engine socket never appeared"
        parent.kill()
        parent.wait()
        assert wait_for(lambda: not sock.exists(), timeout=15), "engine kept running after its parent died"
    finally:
        parent.kill()
        subprocess.run(["pkill", "-f", f"serve --socket {sock}"])


def test_second_engine_refuses(short_tmp):
    home = short_tmp / "m"
    sock1, sock2 = short_tmp / "a.sock", short_tmp / "b.sock"
    p1 = subprocess.Popen([sys.executable, "-m", "misty_engine", "serve", "--socket", str(sock1)],
                          env=env_for(home))
    try:
        assert wait_for(sock1.exists)
        r = subprocess.run([sys.executable, "-m", "misty_engine", "serve", "--socket", str(sock2)],
                           env=env_for(home), capture_output=True, text=True, timeout=30)
        assert r.returncode == 3 and "ENGINE_ALREADY_RUNNING" in r.stderr
    finally:
        p1.terminate()
        p1.wait(timeout=10)


@pytest.fixture
def methods(fctx):
    return build_methods(fctx)


async def test_delete_all_wipes_home_and_keychain(methods, fctx, misty_home):
    keychain.set_secret("openrouter", "k")
    keychain.set_secret("google_token", "t")
    (misty_home.wiki / "x.md").parent.mkdir(parents=True, exist_ok=True)
    (misty_home.wiki / "x.md").write_text("x")
    out = await methods["data.delete_all"]({})
    assert out["deleted"] is True
    assert not misty_home.home.exists()
    assert keychain.get_secret("openrouter") is None and keychain.get_secret("google_token") is None


async def test_set_openrouter_key_never_logged(methods, misty_home, restore_logging):
    from misty_engine.logs import setup_logging
    setup_logging(misty_home.logs)
    await methods["setup.set_openrouter_key"]({"key": "sk-or-v1-NEVERLOGTHIS0123456789abcdef"})
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    assert "NEVERLOGTHIS" not in text and keychain.get_secret("openrouter").startswith("sk-or-v1-")


async def test_settings_set_whitelist_and_ranges(methods, fctx):
    assert (await methods["settings.set"]({"key": "cap_daily_usd", "value": 0.5}))["ok"]
    assert fctx.settings.get("cap_daily_usd") == 0.5
    for bad in ({"key": "paid_calls_allowed", "value": True}, {"key": "cap_daily_usd", "value": -1},
                {"key": "cap_daily_usd", "value": 999}, {"key": "cap_build_usd", "value": True}):
        with pytest.raises(Fatal):
            await methods["settings.set"](bad)
    assert fctx.settings.get("paid_calls_allowed") is False                # the lock cannot be flipped from the app API


async def test_retry_failed_requeues(methods, fctx):
    fctx.queue.enqueue("worth", item_id="it_aaaa000000000001")
    jid = fctx.queue.enqueue("worth", item_id="it_aaaa000000000002")
    fctx.con.execute("UPDATE jobs SET status='failed', error_code='X' WHERE id=?", (jid,))
    assert (await methods["jobs.retry_failed"]({}))["retried"] == 1
    assert fctx.queue.get(jid).status == "queued"


def test_models_adopt_moves_only_named_dirs(tmp_path, monkeypatch):
    from misty_engine import cli
    home = tmp_path / "home"
    hub = home / ".cache/huggingface/hub"
    for d in ("models--mlx-community--embeddinggemma-2-bf16", "models--other--thing"):
        (hub / d).mkdir(parents=True)
        (hub / d / "f").write_text("x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("MISTY_HOME", str(tmp_path / "Misty"))
    assert cli._adopt_models() == 0
    assert (tmp_path / "Misty/models/hub/models--mlx-community--embeddinggemma-2-bf16/f").exists()
    assert not (hub / "models--mlx-community--embeddinggemma-2-bf16").exists()
    assert (hub / "models--other--thing/f").exists()                         # nothing else touched


async def test_retry_failed_survives_two_failed_jobs_for_one_item(methods, fctx):
    """A job fails, the scheduler queues the same item again and that one fails too: Retry must not trip the one-active-job rule."""
    now = fctx.clock.now()
    for jid in ("jb_old", "jb_new"):
        fctx.con.execute("INSERT INTO jobs(id, kind, item_id, status, error_code, next_run_at, created_at, updated_at) "
                         "VALUES (?, 'worth', 'it_aaaa000000000003', 'failed', 'X', ?, ?, ?)", (jid, now, now, now))
    assert (await methods["jobs.retry_failed"]({}))["retried"] == 1
    states = [r[0] for r in fctx.con.execute("SELECT status FROM jobs WHERE item_id='it_aaaa000000000003'")]
    assert sorted(states) == ["failed", "queued"]


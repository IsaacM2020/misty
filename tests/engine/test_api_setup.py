import json

import pytest

from misty_engine import keychain
from misty_engine.api import build_methods
from misty_engine.errors import Fatal
from test_whatsapp import wadb  # noqa: F401  (fixture)

LONG = "The iGEM team met about the wiki and the part documentation deadline. " * 15


@pytest.fixture
def methods(fctx):
    return build_methods(fctx)


async def test_start_build_refuses_while_locked(methods, fctx, seed):
    seed("it_zz00000000000001", LONG)
    with pytest.raises(Fatal) as e:
        await methods["setup.start_build"]({"limit": 1})
    assert e.value.code == "PAID_LOCKED"
    out = await methods["setup.start_build"]({"limit": 1, "dry_run": True})     # estimate is always allowed
    assert out["estimate"]["items"] == 1


async def test_test_openrouter_goes_through_zdr_client(methods, fake_or, fctx):
    out = await methods["setup.test_openrouter"]({})
    assert out["ok"] and out["zdr"] and out["jev_ok"] and out["usd"] > 0
    assert fake_or.request_count == 2
    assert all(json.loads(b)["provider"]["zdr"] is True for b in fake_or.all_payload_texts())


async def test_raise_cap_releases_waiting_cap_jobs(methods, fctx, seed):
    iid = seed("it_zz00000000000002", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fctx.settings.set("cap_build_usd", 0.0000001)
    from misty_engine.pipeline import Worker
    jid = fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid], "build_id": "bd_000000000001"}, cloud=True)
    await Worker(fctx, {"compile_batch": 1}).run_until_idle(["compile_batch"])
    assert fctx.queue.get(jid).status == "waiting_cap"
    out = await methods["control.raise_cap"]({"scope": "build", "usd": 3.0})
    assert out["released"] == 1 and fctx.settings.get("cap_build_usd") == 3.0
    assert fctx.queue.get(jid).status == "queued"
    with pytest.raises(Fatal):
        await methods["control.raise_cap"]({"scope": "weird", "usd": 1})


async def test_save_sources_and_key_storage(methods, fctx):
    await methods["setup.save_sources"]({"enabled": {"gmail": True, "bogus": True}, "imessage_excluded": ["chat1"]})
    en = fctx.settings.get("sources_enabled")
    assert en["gmail"] is True and "bogus" not in en and fctx.settings.get("imessage_excluded") == ["chat1"]
    await methods["setup.set_openrouter_key"]({"key": "sk-or-v1-abc"})
    assert keychain.get_secret("openrouter") == "sk-or-v1-abc"
    with pytest.raises(Fatal):
        await methods["setup.set_openrouter_key"]({"key": "  "})


async def test_check_fda_false_without_access(methods, monkeypatch, tmp_path):
    from misty_engine.sources import imessage
    monkeypatch.setattr(imessage, "DEFAULT_DB", tmp_path / "nope.db")
    import misty_engine.sources.imessage as m
    monkeypatch.setattr(m.IMessageSource, "__init__", lambda self, *a, **k: (
        setattr(self, "db_path", tmp_path / "nope.db") or None))
    assert (await methods["setup.check_fda"]({}))["granted"] is False


async def test_build_over_rpc_runs_and_reports(methods, fctx, seed, fake_or):
    for i in range(6):
        seed(f"it_rb0000000000000{i}", LONG + str(i), title=f"n{i}.md")
    fctx.settings.set("paid_calls_allowed", True)
    out = await methods["setup.start_build"]({"limit": 6})
    assert out["build_id"].startswith("bd_")
    import asyncio
    for _ in range(100):
        st = (await methods["build.status"]({}))["build"]
        if st and st["phase"] == "done":
            break
        await asyncio.sleep(0.1)
    assert st["phase"] == "done" and st["done"] == 6


async def test_saved_state_reports_what_is_already_set_up_without_network(methods, fake_or):
    out = await methods["setup.saved_state"]({})
    assert out == {"openrouter_key": False, "google_email": None}
    keychain.set_secret("openrouter", "sk-or-v1-test")
    keychain.set_secret("google_token", "{}")
    out = await methods["setup.saved_state"]({})
    assert out == {"openrouter_key": True, "google_email": "you@example.com"}
    assert fake_or.request_count == 0                                        # no paid or network call


async def test_start_build_from_the_app_button_is_the_users_yes(methods, fctx, seed):
    seed("it_zz00000000000009", LONG)
    with pytest.raises(Fatal):                                    # CLI / no approval: still locked
        await methods["setup.start_build"]({"limit": 1})
    assert fctx.settings.get("paid_calls_allowed") is not True
    out = await methods["setup.start_build"]({"limit": 1, "user_approved": True})
    assert fctx.settings.get("paid_calls_allowed") is True and out


async def test_chat_selection_is_readable_and_applies_live(methods, fctx, wadb, monkeypatch):
    """The Settings chat picker: read the current picks, save new ones, the engine reads them without a restart."""
    from misty_engine import pipeline
    from misty_engine.sources.whatsapp import WhatsAppSource
    J1 = "6591234567@s.whatsapp.net"
    monkeypatch.setattr(pipeline, "build_live_sources", lambda st: (
        {"whatsapp": WhatsAppSource(wadb, picked=set(st.get("whatsapp_picked")))}
        if st.get("sources_enabled").get("whatsapp") else {}))
    fctx.settings.set("phase", "live")
    start = await methods["setup.chat_selection"]({})
    assert start == {"imessage_excluded": [], "whatsapp_picked": [],
                     "enabled": {"imessage": False, "whatsapp": False}}
    assert "whatsapp" not in fctx.sources

    await methods["setup.save_sources"]({"enabled": {"whatsapp": True}, "whatsapp_picked": [J1]})
    assert fctx.sources["whatsapp"].picked == {J1}
    queued = fctx.con.execute("SELECT payload FROM jobs WHERE kind='discover' AND status='queued'").fetchall()
    assert any(json.loads(r[0])["source"] == "whatsapp" for r in queued)
    assert (await methods["setup.chat_selection"]({}))["whatsapp_picked"] == [J1]

    await methods["setup.save_sources"]({"enabled": {"whatsapp": False}, "whatsapp_picked": []})
    assert "whatsapp" not in fctx.sources                                    # unpicking everything stops reading it


async def test_saving_chats_during_first_setup_queues_nothing(methods, fctx):
    fctx.settings.set("phase", "setup")
    await methods["setup.save_sources"]({"enabled": {"whatsapp": True}, "whatsapp_picked": ["x@g.us"]})
    assert fctx.con.execute("SELECT COUNT(*) FROM jobs WHERE kind='discover'").fetchone()[0] == 0



async def test_add_folders_merges_and_never_replaces(methods, fctx, tmp_path):
    a, b = tmp_path / "physics", tmp_path / "chem"
    a.mkdir(); b.mkdir()
    before = list(fctx.settings.get("folders"))
    r = await methods["settings.add_folders"]({"paths": [str(a), str(b), str(a)]})
    assert r["folders"][:len(before)] == before and r["folders"].count(str(a.resolve())) == 1 and str(b.resolve()) in r["folders"]
    again = await methods["settings.add_folders"]({"paths": [str(a)]})
    assert again["folders"] == r["folders"]
    with pytest.raises(Fatal):
        await methods["settings.add_folders"]({"paths": [str(tmp_path / "nope")]})


async def test_wake_word_setting_defaults_and_changes(methods, fctx):
    """The wake word is on for "edith" by default; the app changes pieces of it and bad values are refused."""
    out = await methods["wake.get"]({})
    assert out == {"enabled": True, "word": "edith", "sensitivity": 0.5}
    out = await methods["wake.set"]({"enabled": False})
    assert out == {"enabled": False, "word": "edith", "sensitivity": 0.5}
    assert fctx.settings.get("wake_word")["enabled"] is False
    out = await methods["wake.set"]({"sensitivity": 0.8, "word": " Edith "})
    assert out == {"enabled": False, "word": "edith", "sensitivity": 0.8}
    for bad in ({"enabled": "yes"}, {"sensitivity": 1.5}, {"sensitivity": True}, {"word": ""}, {"volume": 3}):
        with pytest.raises(Fatal) as e:
            await methods["wake.set"](bad)
        assert e.value.code == "BAD_VALUE"
    assert await methods["wake.get"]({}) == {"enabled": False, "word": "edith", "sensitivity": 0.8}      # a refusal changes nothing


async def test_wake_word_get_fills_keys_missing_from_an_older_stored_value(methods, fctx):
    fctx.settings.set("wake_word", {"enabled": False})
    assert await methods["wake.get"]({}) == {"enabled": False, "word": "edith", "sensitivity": 0.5}

import json
import re

import numpy as np

from misty_engine.compile.prompts import BatchItem, render_batch
from misty_engine.registry import hints
from misty_engine.registry.store import Registry
from misty_engine.scrub import find_secrets


def world(fctx, seed):
    reg = Registry(fctx.con, fctx.clock)
    out = {}
    out["aarav"] = reg.new_entity("person", "Aarav Shah", status="active", path="people/aarav-shah.md", known=True)
    out["janaki"] = reg.new_entity("person", "Ms Janaki Kota", status="active", path="people/janaki-kota.md", known=True)
    out["igem"] = reg.new_entity("project", "iGEM wiki", status="active", path="projects/igem-wiki.md", known=True)
    out["sat"] = reg.new_entity("topic", "SAT preparation", status="active", path="topics/sat.md", known=True)
    reg.add_alias(out["aarav"], "Aarav")
    reg.add_alias(out["janaki"], "Janaki")
    return reg, out


def rows_for(fctx, seed, text, iid="it_hint00000000001", **meta):
    seed(iid, text, source="whatsapp" if "chat_id" in meta else "gmail", kind="chat_week" if "chat_id" in meta else "mail_thread")
    fctx.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps(meta), iid))
    return fctx.con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchall()


def test_empty_registry_gives_no_block(fctx, seed):
    assert hints.known_block(fctx, rows_for(fctx, seed, "Aarav sent the plan")) == ""


def test_only_names_present_or_contextual(fctx, seed):
    world(fctx, seed)
    block = hints.known_block(fctx, rows_for(fctx, seed, "Aarav moved the wiki sync to Friday"))
    assert "Aarav Shah" in block and "Ms Janaki Kota" not in block and block.startswith("Known names")


def test_block_lists_people_first_then_projects_then_topics(fctx, seed):
    world(fctx, seed)
    block = hints.known_block(fctx, rows_for(fctx, seed, "Aarav and Janaki discussed the igem wiki and SAT preparation"))
    labels = re.findall(r"^(People|Projects|Topics):", block, re.M)
    assert labels == ["People", "Projects", "Topics"]
    assert "Aarav Shah; Ms Janaki Kota" in block or "Ms Janaki Kota; Aarav Shah" in block


def test_counterpart_and_recent_chat_names_come_along(fctx, seed):
    reg, w = world(fctx, seed)
    reg.add_handle(w["aarav"], "phone", "+6591234567")
    # an earlier note of the same chat mentioned Janaki
    seed("it_old0000000000001", "x", source="whatsapp", kind="chat_week")
    fctx.con.execute("UPDATE items SET meta=? WHERE id='it_old0000000000001'", (json.dumps({"chat_id": "c1"}),))
    reg.add_mention("it_old0000000000001", w["janaki"], "Janaki")
    rows = rows_for(fctx, seed, "see you there", chat_id="c1",
                    counterpart={"name": None, "handle": ["phone", "+6591234567"], "from": "chat"})
    block = hints.known_block(fctx, rows)
    assert "Aarav Shah" in block and "Ms Janaki Kota" in block


def test_block_is_capped(fctx, seed):
    reg = Registry(fctx.con, fctx.clock)
    names = []
    for i in range(200):
        n = f"Person{i:03d} Surname{i:03d}"
        names.append(n)
        reg.new_entity("person", n, status="active", path=f"people/p{i}.md", known=True)
    rows = rows_for(fctx, seed, " ".join(n.split()[0] + " " + n.split()[1] for n in names))
    block = hints.known_block(fctx, rows)
    listed = [x for x in re.split(r"[;\n]", block.split("\n", 1)[1].split(":", 1)[1]) if x.strip()]
    assert len(listed) <= 40


def test_block_never_contains_secret_shaped_names(fctx, seed):
    reg = Registry(fctx.con, fctx.clock)
    reg.new_entity("person", "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef", status="active",
                   path="people/x.md", known=True)
    a = reg.new_entity("person", "Aarav Shah", status="active", path="people/a.md", known=True)
    reg.add_alias(a, "Aarav")
    block = hints.known_block(fctx, rows_for(fctx, seed, "Aarav and sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"))
    assert find_secrets(block, "file") == [] and "Aarav Shah" in block


def test_entities_with_only_private_sources_are_not_offered(fctx, seed):
    reg = Registry(fctx.con, fctx.clock)
    e = reg.new_entity("person", "Secret Friend", status="candidate")
    seed("it_priv000000000001", "x", source="whatsapp", kind="chat_week")
    fctx.con.execute("UPDATE items SET meta=? WHERE id='it_priv000000000001'", (json.dumps({"chat_id": "cp"}),))
    reg.add_mention("it_priv000000000001", e, "Secret Friend")
    fctx.settings.set("context_private_chats", ["whatsapp:cp"])
    assert hints.known_block(fctx, rows_for(fctx, seed, "Secret Friend wrote")) == ""


def test_group_and_with_tags_and_known_in_user_message():
    t = render_batch([BatchItem("it_1", "whatsapp", "chat_week", "MC SA2 OVER · 2026-W20", "hi", group="MC SA2 OVER"),
                      BatchItem("it_2", "whatsapp", "chat_week", "Arjun · 2026-W20", "yo", with_name="Arjun")],
                     "2026-10-15", known="People: Aarav Shah")
    assert 'group="MC SA2 OVER"' in t and 'with="Arjun"' in t
    assert t.index("Known names") < t.index("<item") and "People: Aarav Shah" in t

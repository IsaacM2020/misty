"""Step C7: the activity trail. Stretch merging, the 20 second floor, private apps skipped, pause, retention, grouping."""
from misty_engine import activity, privacy


def at(ctx, t):
    ctx.clock.now = lambda: t


def rows(ctx):
    return ctx.con.execute("SELECT * FROM activity ORDER BY id").fetchall()


def test_a_stretch_is_kept_and_the_same_thing_merges(ctx):
    privacy.seed_defaults(ctx)
    at(ctx, 1000); activity.note(ctx, "com.apple.Pages", "Pages", "Chem notes", "/a/notes.pages")
    at(ctx, 1060); assert activity.note(ctx, "com.apple.Pages", "Pages", "Chem notes", "/a/notes.pages")["merged"]
    at(ctx, 1100); activity.note(ctx, "com.apple.Safari", "Safari", "Wiki", "")
    r = rows(ctx)
    assert len(r) == 2 and r[0]["until"] - r[0]["at"] >= 60 and r[0]["app"] == "Pages"


def test_a_pass_through_under_20_seconds_is_not_kept(ctx):
    privacy.seed_defaults(ctx)
    at(ctx, 1000); activity.note(ctx, "com.apple.Pages", "Pages", "A", "")
    at(ctx, 1010); activity.note(ctx, "com.apple.Safari", "Safari", "B", "")
    at(ctx, 1100); activity.note(ctx, "com.apple.Notes", "Notes", "C", "")
    assert [r["title"] for r in rows(ctx)] == ["B", "C"] and rows(ctx)[0]["until"] - rows(ctx)[0]["at"] >= 20


def test_private_apps_are_never_saved(ctx):
    privacy.seed_defaults(ctx)
    at(ctx, 1000); activity.note(ctx, "com.apple.Pages", "Pages", "A", "")
    at(ctx, 1100); assert activity.note(ctx, "com.apple.keychainaccess", "Keychain Access", "Passwords", "")["kept"] is False
    at(ctx, 1300); activity.note(ctx, "com.apple.Safari", "Safari", "B", "")
    assert all(r["bundle"] != "com.apple.keychainaccess" for r in rows(ctx))


def test_pausing_it_stops_writing(ctx):
    privacy.seed_defaults(ctx)
    ctx.settings.set("activity_trail", False)
    at(ctx, 1000); assert activity.note(ctx, "com.apple.Pages", "Pages", "A", "")["kept"] is False
    assert rows(ctx) == [] and activity.stretches(ctx)["trail_off"]


def test_retention_30_days(ctx):
    privacy.seed_defaults(ctx)
    now = 40 * 86400
    ctx.con.execute("INSERT INTO activity(at, until, bundle, app) VALUES (?,?,?,?)", (1000, 2000, "b", "old"))
    ctx.con.execute("INSERT INTO activity(at, until, bundle, app) VALUES (?,?,?,?)", (now - 100, now - 10, "b", "new"))
    ctx.con.commit()
    assert activity.prune(ctx, now) == 1 and [r["app"] for r in rows(ctx)] == ["new"]


def test_grouping_by_document_with_minutes(ctx):
    privacy.seed_defaults(ctx)
    base = 1_762_300_800                     # 2025-11-05 00:00 UTC = 08:00 SGT
    for t, app, title, doc in [(0, "Pages", "Chem", "/c.pages"), (600, "Safari", "Wiki", ""), (1200, "Pages", "Chem", "/c.pages"), (2400, "Notes", "x", "")]:
        at(ctx, base + t); activity.note(ctx, "b." + app, app, title, doc)
    at(ctx, base + 2700); activity.note(ctx, "b.End", "End", "", "")
    out = activity.stretches(ctx, "2025-11-05", "8:00", "9:00")
    got = {s["app"]: s["minutes"] for s in out["stretches"]}
    assert got == {"Pages": 30, "Safari": 10, "Notes": 5}                  # Pages twice on the same doc: 10 + 20 min, grouped
    assert out["stretches"][0]["app"] == "Pages"
    assert activity.stretches(ctx, "2025-11-05", "8:00", "8:15")["stretches"][0]["minutes"] in (10, 15)

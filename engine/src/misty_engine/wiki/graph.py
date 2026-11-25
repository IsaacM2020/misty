"""The graph the app draws, in levels (spec 3.8): you in the centre, the life areas as ring 1, entities of an
*expanded* area as ring 2, recent activity glowing. Built from the registry, never from page files.

Default view: Me, every area, the top 5 entities of each (under 60 nodes). Clicking an area expands it to its top 12
with a "+N more" count. Chat-member-only people fold into one group node per chat. Three edge kinds only:
belongs (solid), involves (person with a project or topic, 2+ shared notes), related (project or topic pair, 3+).
Chats the person keeps out of context packs contribute nothing: no node, no count, no glow, no date."""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from ..clock import sgt_date
from ..ctx import Ctx
from ..registry import privacy
from ..registry.names import core_tokens, norm
from ..registry.store import Registry

DEFAULT_TOP = 5
EXPAND_TOP = 12
MIN_SOURCES_NODE = 3
GLOW_DAYS, GLOW_FULL = 7, 5
MAX_DEFAULT_NODES = 60
MAX_EDGES_PER_NODE = 3
MAX_GROUPS = 3
SEARCH_MAX = 8
MIN_GROUP_MEMBERS = 2


def graph_score(sources: int, days_since: int | None, degree: int, importance: int) -> float:
    """0.35 x log(sources) + 0.30 x recency + 0.20 x degree + 0.15 x importance, each scaled to 0 to 1."""
    if days_since is None:
        recency = 0.0
    else:
        recency = 1.0 if days_since <= 30 else max(0.0, 1 - (days_since - 30) / 335)
    return (0.35 * math.log1p(sources) / math.log1p(50) + 0.30 * recency
            + 0.20 * min(degree, 10) / 10 + 0.15 * max(0, min(importance, 5)) / 5)


def _areas(ctx: Ctx) -> list[dict]:
    from .areas import read_areas
    return [{"slug": a["slug"], "title": a["title"], "importance": int(a["importance"] or 3)}
            for a in read_areas(ctx) if a.get("status") != "dropped"]


def build_graph(ctx: Ctx, *, expanded=(), focus: str | None = None, q: str | None = None,
                user_label: str = "") -> dict:
    reg = Registry(ctx.con, ctx.clock)
    today = date.fromisoformat(sgt_date(ctx.clock.now()))
    today_iso = today.isoformat()
    since = (today - timedelta(days=GLOW_DAYS)).isoformat()
    keys = privacy.private_keys(ctx.settings)

    # ---- public facts per entity, in one pass over the mentions
    ents = {e["id"]: e for e in reg.entities()}
    notes: dict[str, set[str]] = defaultdict(set)              # entity -> public item ids
    only_chat: dict[str, bool] = {}                            # entity -> every public mention is a chat week
    chat_of: dict[str, Counter] = defaultdict(Counter)         # entity -> chat key -> n
    chat_title: dict[str, str] = {}
    dates: dict[str, list[str]] = defaultdict(list)
    members_of: dict[str, set[str]] = defaultdict(set)         # item -> entities
    for r in ctx.con.execute(
            "SELECT m.entity_id, i.id, i.source, i.kind, i.title, i.modified_at, i.meta FROM mentions m "
            "JOIN items i ON i.id = m.item_id"):
        eid = r["entity_id"]
        if eid not in ents:
            continue
        meta = json.loads(r["meta"] or "{}")
        if privacy.item_is_private(r["source"], meta, keys):
            continue
        notes[eid].add(r["id"])
        members_of[r["id"]].add(eid)
        if r["kind"] != "chat_week":
            only_chat[eid] = False
        else:
            only_chat.setdefault(eid, True)
            ck = f"{r['source']}:{meta.get('chat_id')}"
            chat_of[eid][ck] += 1
            chat_title.setdefault(ck, (r["title"] or "").split(" · ")[0].strip() or "Chat")
        if r["modified_at"] and r["modified_at"][:10] <= today_iso:
            dates[eid].append(r["modified_at"][:10])
        for d, _ in meta.get("dated_lines") or []:
            if str(d)[:10] <= today_iso:
                dates[eid].append(str(d)[:10])

    def summary_ok(eid: str) -> bool:
        t = ctx.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (eid,)).fetchone()
        return bool(t and (t["summary"] or "").strip() not in ("", "(stub)"))

    # ---- degree: distinct co-mentioned entities in public notes
    co: dict[str, Counter] = defaultdict(Counter)
    for item, group in members_of.items():
        g = sorted(group)
        for a in g:
            for b in g:
                if a != b:
                    co[a][b] += 1

    # ---- eligible nodes and folded chat-only people
    eligible: dict[str, dict] = {}
    folded: dict[str, set[str]] = defaultdict(set)             # chat key -> person entity ids
    for eid, e in ents.items():
        n = len(notes.get(eid, ()))
        if n == 0 or e["kind"] not in ("person", "project", "topic"):
            continue
        has_page = e["status"] == "active" and bool(e["path"]) and summary_ok(eid)
        if not has_page:
            if (e["kind"] == "person" and only_chat.get(eid, False) and n < MIN_SOURCES_NODE
                    and chat_of.get(eid)):
                folded[chat_of[eid].most_common(1)[0][0]].add(eid)
            continue
        if n < MIN_SOURCES_NODE and not (e["known"] or e["origin"] in ("contact", "user")):
            continue
        past = max(dates[eid]) if dates.get(eid) else None
        days = (today - date.fromisoformat(past)).days if past else None
        recent = sum(1 for d in dates.get(eid, ()) if d >= since)
        eligible[eid] = {
            "id": e["path"][:-3], "label": e["name"], "type": e["kind"], "area": e["area"],
            "importance": int(e["importance"] or 3), "glow": round(min(1.0, recent / GLOW_FULL), 3),
            "last_date": past, "sources": n, "_eid": eid,
            "_score": graph_score(n, days, len(co.get(eid, ())), int(e["importance"] or 3))}

    areas = _areas(ctx)
    area_slugs = {a["slug"] for a in areas}
    by_area: dict[str, list[dict]] = defaultdict(list)
    loose: list[dict] = []
    for n in eligible.values():
        (by_area[n["area"]] if n["area"] in area_slugs else loose).append(n)
    for lst in by_area.values():
        lst.sort(key=lambda n: (-n["_score"], n["id"]))
    loose.sort(key=lambda n: (-n["_score"], n["id"]))

    groups = []
    for ck, members in folded.items():
        if len(members) >= MIN_GROUP_MEMBERS:
            groups.append({"id": f"group/{ck}", "label": chat_title.get(ck, "Chat"), "type": "group", "area": None,
                           "importance": 2, "glow": 0.0, "last_date": None, "sources": 0, "members": len(members)})
    groups.sort(key=lambda g: (-g["members"], g["id"]))
    groups = groups[:MAX_GROUPS]

    # ---- what to show
    exp = {a if a.startswith("areas/") else f"areas/{a}" for a in (expanded or ())}
    force: set[str] = set()
    if focus:
        f = next((n for n in eligible.values() if n["id"] == focus or n["id"] + ".md" == focus), None)
        if f is not None:
            force.add(f["id"])
            if f["area"] in area_slugs:
                exp.add(f"areas/{f['area']}")
    matched: list[dict] = []
    if q and q.strip():
        qn = norm(q)
        toks = [t for t in core_tokens(q, "person")] or qn.split()       # "Ms Janaki" is "janaki"
        for n in eligible.values():
            names = {norm(n["label"])} | {a["alias_norm"] for a in ctx.con.execute(
                "SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (n["_eid"],))}
            if any(qn in nm or (toks and all(t in nm.split() or t in nm for t in toks)) for nm in names):
                matched.append(n)
        matched.sort(key=lambda n: (-n["_score"], n["id"]))
        matched = matched[:SEARCH_MAX]
        for n in matched:
            force.add(n["id"])
            if n["area"] in area_slugs:
                exp.add(f"areas/{n['area']}")

    per_area = DEFAULT_TOP
    if areas:
        room = MAX_DEFAULT_NODES - 1 - len(areas) - len(groups)
        per_area = max(1, min(DEFAULT_TOP, room // len(areas)))
    shown: dict[str, dict] = {}
    area_nodes = []
    for a in areas:
        nid = f"areas/{a['slug']}"
        mine = by_area.get(a["slug"], [])
        is_exp = nid in exp
        cut = EXPAND_TOP if is_exp else per_area
        picked = mine[:cut]
        for n in mine[cut:]:
            if n["id"] in force:
                picked.append(n)
        for n in picked:
            shown[n["id"]] = n
        area_nodes.append({"id": nid, "label": a["title"], "type": "area", "area": None,
                           "importance": a["importance"], "glow": 0.0, "last_date": None,
                           "sources": sum(len(notes.get(n["_eid"], ())) for n in mine),
                           "more": max(0, len(mine) - len(picked)), "expanded": is_exp})
    if not areas:                                              # no life areas yet: the strongest few around Me
        for n in loose[:EXPAND_TOP]:
            shown[n["id"]] = n
    for n in loose:
        if n["id"] in force:
            shown[n["id"]] = n

    # ---- edges
    out_nodes = [{"id": "me", "label": user_label.strip() or "Me", "type": "self", "area": None, "importance": 5,
                  "glow": 0.0, "last_date": None, "sources": 0}]
    edges: list[dict] = []
    for a in area_nodes:
        out_nodes.append(a)
        edges.append({"source": "me", "target": a["id"], "kind": "belongs"})
    for n in sorted(shown.values(), key=lambda n: n["id"]):
        node = {k: v for k, v in n.items() if not k.startswith("_")}
        out_nodes.append(node)
        tgt = f"areas/{n['area']}" if n["area"] in area_slugs else "me"
        edges.append({"source": n["id"], "target": tgt, "kind": "belongs"})
    for g in groups:
        out_nodes.append(g)
        edges.append({"source": g["id"], "target": "me", "kind": "belongs"})
    by_eid = {n["_eid"]: n for n in shown.values()}
    cand: list[tuple[str, str, str, int]] = []
    seen = set()
    for a_eid, others in co.items():
        if a_eid not in by_eid:
            continue
        for b_eid, w in others.items():
            if b_eid not in by_eid or (b_eid, a_eid) in seen:
                continue
            seen.add((a_eid, b_eid))
            ta, tb = ents[a_eid]["kind"], ents[b_eid]["kind"]
            lo, hi = sorted((by_eid[a_eid]["id"], by_eid[b_eid]["id"]))     # random entity ids must not order edges
            if "person" in (ta, tb) and ta != tb and w >= 2:
                cand.append((lo, hi, "involves", w))
            elif ta != "person" and tb != "person" and w >= 3:
                cand.append((lo, hi, "related", w))
    cand.sort(key=lambda c: (-c[3], c[0], c[1]))
    used: Counter = Counter()
    for s, t, kind, w in cand:
        if used[s] >= MAX_EDGES_PER_NODE or used[t] >= MAX_EDGES_PER_NODE:
            continue
        used[s] += 1
        used[t] += 1
        edges.append({"source": s, "target": t, "kind": kind, "weight": w})
    return {"nodes": out_nodes, "edges": edges}

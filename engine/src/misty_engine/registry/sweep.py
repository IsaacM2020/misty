"""The resolution sweep (spec 3.2): who is this mention?

Resolution is a batch step, never a per-note side effect. The sweep answers every mention of a batch of
compiled notes against the registry as it stood when the sweep began (so arrival order cannot matter), clusters
the mentions that are new, asks the judge only about what stays ambiguous, then creates entities.

Properties (each has a test): order independent, idempotent, no page while ambiguous, never a lost mention,
a judge or cap failure parks the work and the next sweep resumes it, a dry run writes nothing and never calls a model.
Entities leave this module as `candidate`; activation needs a summary (Q3) and a page is a rendering (render.py).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace

import numpy as np

from ..ctx import Ctx
from ..errors import CapHit, NetworkDown
from ..logs import log_event
from ..pipeline import handler
from ..types import Job
from ..wiki.notes import section
from . import privacy
from .judge import Question, Side, judge_pairs, pair_key
from .names import canonical_score, compatible, core_tokens, norm
from .resolve import (AliasIndex, Decision, Mention, MentionCtx, conflict_live, entities_compatible,
                      entities_conflict, resolve_mention)
from .store import Registry

MIN_SOURCES = 2
AUTO_COS, AUTO_JACCARD, JUDGE_COS = 0.92, 0.5, 0.80      # defaults; pinned.toml [resolve] holds the calibrated ones
_DEFAULTS = {"auto_cos": 0.92, "auto_jaccard": 0.5, "judge_cos": 0.80, "min_sources": 2}


OVERRIDES: dict[str, float] = {}        # set by the calibration grid for the length of one run


def _t(ctx, name: str):
    """A threshold: the pinned [resolve] value, unless a caller (a test, the calibration grid) set the module
    constant to something else, which wins."""
    if name in OVERRIDES:
        return OVERRIDES[name]
    mod = {"auto_cos": AUTO_COS, "auto_jaccard": AUTO_JACCARD, "judge_cos": JUDGE_COS, "min_sources": MIN_SOURCES}[name]
    if mod != _DEFAULTS[name]:
        return mod
    return getattr(ctx.pinned.resolve, name)

NEAR_IDENTICAL_COS = 0.95                                 # without a shared word, only near-identical names are asked
TOP_K = 5                                                 # each entity is compared with its 5 nearest names, not all
ROUNDS = 3                                                # merges change spellings: ask again, at most 3 times
STOP_WORDS = frozenset({"the", "of", "and", "a", "an", "for", "in", "to", "on", "at", "with", "my", "our"})
GENERIC_DF = 0.25                                         # a topic in this share of all notes is a word, not a thing
GENERIC_MIN_ITEMS = 100                                   # ... and only once the corpus is big enough to tell
KIND_KEYS = (("people", "person"), ("projects", "project"), ("topics", "topic"))


@dataclass
class SweepReport:
    items: int = 0
    mentions: int = 0
    resolved: int = 0
    created: int = 0
    plain: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    merges: dict[str, int] = field(default_factory=dict)          # reason -> n
    judge_pairs: int = 0                                          # pairs the model was asked this sweep
    judge_pairs_needed: int = 0                                   # pairs without a cached verdict (dry run: what it would cost)
    pending: int = 0
    deferred: int = 0                                             # items parked because a verdict was unavailable
    qualifying: list[str] = field(default_factory=list)           # entity ids ready for a summary
    settled: list[str] = field(default_factory=list)              # item ids whose mentions are all decided
    extra: dict = field(default_factory=dict)                     # rebuild adds before/after counts here

    def counts(self) -> dict:
        """Counts only: safe to return over the wire and to log."""
        d = dict(self.__dict__)
        d["qualifying"] = len(self.qualifying)
        d["settled"] = len(self.settled)
        return d


# ------------------------------------------------------------------ item -> mentions

def _chat_kind(meta: dict) -> str:
    k = meta.get("chat_kind")
    if k in ("1to1", "group"):
        return k
    cid = meta.get("chat_id") or ""
    if not cid:
        return ""
    if ";+;" in cid or cid.endswith("@g.us"):
        return "group"
    if ";-;" in cid or cid.endswith("@s.whatsapp.net"):
        return "1to1"
    return ""


def _chat_title(row, meta: dict) -> str:
    if meta.get("chat_title"):
        return str(meta["chat_title"])
    if meta.get("chat_id") and " · " in (row["title"] or ""):
        return row["title"].split(" · ")[0].strip()
    return ""


def _handle(value) -> tuple[str, str] | None:
    """Normalise a sender, jid or chat id to ("email", x) or ("phone", "+digits"). A jid's user part is a phone."""
    v = str(value or "").strip()
    if not v:
        return None
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", v)
    if m and not v.endswith("@s.whatsapp.net") and not v.endswith("@g.us"):
        return ("email", m.group(0).lower())
    digits = re.sub(r"\D", "", v.split("@")[0].rsplit(";", 1)[-1])
    if 8 <= len(digits) <= 15:
        return ("phone", "+" + digits)
    return None


def _pair(v) -> tuple[str, str] | None:
    """A stored handle ["phone", "+65..."] (or a raw string from an older item) as a normalised pair."""
    if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] in ("phone", "email", "jid") and v[1]:
        return (str(v[0]), str(v[1]))
    return _handle(v) if isinstance(v, str) else None


def _counterpart(meta: dict) -> tuple[str | None, tuple[str, str] | None, str]:
    """(name, handle, origin) of a 1:1 chat's other person: the Task 3.1 dict, or an older plain string."""
    cp = meta.get("counterpart")
    if isinstance(cp, dict):
        name = cp.get("name") if isinstance(cp.get("name"), str) and cp["name"].strip() else None
        handle = _pair(cp.get("handle")) or _pair(meta.get("counterpart_handle"))
        return name, handle, "contact" if cp.get("from") == "contacts" else "chat"
    if isinstance(cp, str) and cp.strip():
        return cp.strip(), _pair(meta.get("counterpart_handle")), "chat"
    return None, _pair(meta.get("counterpart_handle")), "chat"


def _counterpart_handle(meta: dict) -> tuple[str, str] | None:
    h = _counterpart(meta)[1]
    if h:
        return h
    if _chat_kind(meta) == "1to1":
        return _handle(meta.get("chat_id"))
    return None


def mentions_for_item(ctx: Ctx, row) -> list[Mention]:
    """People, projects and topics named by the note, plus the chat's counterpart (first, role counterpart)."""
    meta = json.loads(row["meta"] or "{}")
    kind = _chat_kind(meta)
    sender = _pair(meta.get("sender_handle")) or _handle(meta.get("sender")) or \
        (_counterpart_handle(meta) if kind == "1to1" else None)
    mc = MentionCtx(source=row["source"], chat_id=str(meta.get("chat_id") or ""), chat_title=_chat_title(row, meta),
                    chat_kind=kind, sender_handle=sender)
    out: list[Mention] = []
    cname, _, corigin = _counterpart(meta)
    if cname:
        out.append(Mention(row["id"], "person", cname, "counterpart", mc, corigin))
    for key, k in KIND_KEYS:
        for raw in meta.get(key) or []:
            if isinstance(raw, str) and raw.strip():
                out.append(Mention(row["id"], k, raw.strip(), "named", mc))
    return out


# ------------------------------------------------------------------ known names and qualification

def _known_context(ctx: Ctx) -> tuple[set[str], str]:
    names: set[str] = set()
    hints = (ctx.extras or {}).get("graph_hints")
    if hints is not None:
        names |= {norm(l) for l in hints.labels() if norm(l)}
    return names, norm(ctx.card())


def is_known(ctx: Ctx, reg: Registry, e) -> bool:
    """Contacts, the about card, graphify labels, accepted area members: names that are real with one mention."""
    if e["known"] or e["origin"] in ("contact", "user"):
        return True
    labels, card = _known_context(ctx)
    for a in reg.aliases(e["id"]):
        n = a["alias_norm"]
        if n in labels or (len(n) >= 4 and f" {n} " in f" {card} "):
            return True
    return False


def public_source_count(ctx: Ctx, eid: str) -> int:
    keys = privacy.private_keys(ctx.settings)
    n = 0
    for r in ctx.con.execute(
            "SELECT DISTINCT i.id, i.source, i.meta FROM mentions m JOIN items i ON i.id = m.item_id "
            "WHERE m.entity_id=?", (eid,)):
        if not privacy.item_is_private(r["source"], json.loads(r["meta"] or "{}"), keys):
            n += 1
    return n


def qualifies(ctx: Ctx, eid: str, *, ignore_pending: bool = False) -> bool:
    """Ready for a summary (and then a page): at least 2 public sources or a known name, and nothing ambiguous.
    `ignore_pending` answers "would it qualify once the judge has spoken", used by dry-run estimates."""
    reg = Registry(ctx.con, ctx.clock)
    e = reg.get(eid)
    if e is None or e["status"] not in ("candidate", "active") or (e["pending"] and not ignore_pending):
        return False
    return public_source_count(ctx, eid) >= _t(ctx, "min_sources") or is_known(ctx, reg, e)


# ------------------------------------------------------------------ internals

@dataclass
class Occ:
    item_id: str
    raw: str
    role: str
    ctx: MentionCtx
    kind: str
    cp_core: tuple[str, ...] = ()        # core tokens of the item's counterpart, when the counterpart is new
    origin: str = ""


@dataclass
class ItemWork:
    row: object
    meta: dict
    mentions: list[Mention]
    decisions: list[Decision]
    final: list[tuple[str, str | None]] = field(default_factory=list)      # (action, entity id) per mention
    deferred: bool = False
    plain_n: int = 0


@dataclass
class Cluster:
    kind: str
    members: dict[tuple, list[Occ]] = field(default_factory=dict)         # core tokens -> occurrences
    attach: str | None = None                                             # existing entity it resolves to (R6)
    eid: str | None = None
    pending: bool = False
    text: str = ""

    @property
    def occs(self) -> list[Occ]:
        return [o for t in sorted(self.members) for o in self.members[t]]

    def best_name(self) -> str:
        counts: dict[str, int] = {}
        for o in self.occs:
            counts[o.raw] = counts.get(o.raw, 0) + 1
        return max(counts, key=lambda s: (canonical_score(s, counts[s]), s))


def _first_sentence(text: str, limit: int = 160) -> str:
    t = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:limit]


def _note_summary(ctx: Ctx, note_path: str | None) -> str:
    if not note_path:
        return ""
    try:
        page = ctx.wiki.read(note_path) if ctx.wiki else None
    except Exception:
        return ""
    return _first_sentence(section(page.body, "Summary")) if page else ""


def _vec_blob(v) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _entity_line(ctx: Ctx, eid: str) -> str:
    """One line describing an entity for the judge: its summary, else a public note's first sentence."""
    r = ctx.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (eid,)).fetchone()
    if r and (r["summary"] or "").strip() not in ("", "(stub)"):
        return _first_sentence(r["summary"])
    keys = privacy.private_keys(ctx.settings)
    for r in ctx.con.execute(
            "SELECT i.note_path, i.source, i.meta FROM mentions m JOIN items i ON i.id = m.item_id "
            "WHERE m.entity_id=? ORDER BY i.modified_at DESC, i.id LIMIT 12", (eid,)):
        if not privacy.item_is_private(r["source"], json.loads(r["meta"] or "{}"), keys):
            return _note_summary(ctx, r["note_path"])
    return ""


def _entity_side(ctx: Ctx, reg: Registry, eid: str) -> Side:
    e = reg.get(eid)
    others = [a["alias"] for a in reg.aliases(eid) if norm(a["alias"]) != norm(e["name"])]
    return Side(key=eid, kind=e["kind"], name=e["name"], aliases=tuple(others[:4]),
                line=_entity_line(ctx, eid), n=reg.mention_count(eid))


def _entity_text_for_vec(ctx: Ctx, reg: Registry, eid: str) -> str:
    """What a name is compared by: the name and its other spellings, nothing else. Context lines made two
    different topics from one note look alike (they share the note's sentence), so context is left out."""
    e = reg.get(eid)
    others = [a["alias"] for a in reg.aliases(eid) if norm(a["alias"]) != norm(e["name"])]
    return " / ".join([e["name"], *others[:3]])


def _ensure_vecs(ctx: Ctx, reg: Registry, kind: str) -> dict[str, np.ndarray]:
    """One unit vector per live entity of a kind, cached in entity_vec (this module is its only writer)."""
    rows = reg.entities(kind)
    have = {r["entity_id"]: np.frombuffer(r["vec"], dtype=np.float32) for r in ctx.con.execute(
        "SELECT v.entity_id, v.vec FROM entity_vec v JOIN entities e ON e.id = v.entity_id WHERE e.kind=?", (kind,))}
    todo = [r for r in rows if r["id"] not in have]
    if todo:
        mat = ctx.embedder.embed_documents([_entity_text_for_vec(ctx, reg, r["id"]) for r in todo])
        for r, v in zip(todo, mat):
            ctx.con.execute("INSERT OR REPLACE INTO entity_vec(entity_id, vec) VALUES (?,?)", (r["id"], _vec_blob(v)))
            have[r["id"]] = np.asarray(v, dtype=np.float32)
    return {r["id"]: have[r["id"]] for r in rows}


def _same_marks(a, b) -> bool:
    """"Paper 1" and "Paper 2", "Part A" and "Part B", "Chapter 3" and "Chapter 4" are different things however
    alike they read: numbers and one-letter tokens must match before two names are folded without a judge."""
    marks = lambda ts: {t for t in ts if t.isdigit() or len(t) == 1}      # noqa: E731
    return marks(a) == marks(b)


def _words(tokens) -> set[str]:
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 1}


def _items_by_entity(ctx: Ctx) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in ctx.con.execute("SELECT entity_id, item_id FROM mentions"):
        out.setdefault(r["entity_id"], set()).add(r["item_id"])
    return out


def _alias_token_sets(reg: Registry, eid: str) -> list[set[str]]:
    return [set(t) for t in reg.alias_tokens(eid)]


def _generic_topics(ctx: Ctx) -> set[str]:
    total = ctx.con.execute("SELECT COUNT(*) FROM items WHERE note_path IS NOT NULL").fetchone()[0]
    if total < GENERIC_MIN_ITEMS:
        return set()
    rows = ctx.con.execute(
        "SELECT lower(j.value) AS k, COUNT(DISTINCT i.id) AS n FROM items i, json_each(i.meta, '$.topics') j "
        "WHERE i.note_path IS NOT NULL GROUP BY k").fetchall()
    return {norm(r["k"]) for r in rows if r["n"] / total >= GENERIC_DF}


# ------------------------------------------------------------------ the sweep

async def resolve_sweep(ctx: Ctx, item_ids: list[str] | None = None, *, rebuild: bool = False,
                        dry_run: bool = False, cache_only: bool = False, build_id: str | None = None,
                        job_id: str | None = None, _in_savepoint: bool = False) -> SweepReport:
    """`dry_run` runs the whole sweep inside a savepoint that is rolled back: counts out, nothing written, no model."""
    if not dry_run:
        return await _sweep(ctx, item_ids, rebuild, cache_only, build_id, job_id, _in_savepoint)
    ctx.con.execute("SAVEPOINT misty_dry_run")
    try:
        return await _sweep(ctx, item_ids, rebuild, True, build_id, job_id, True)
    finally:
        ctx.con.execute("ROLLBACK TO misty_dry_run")
        ctx.con.execute("RELEASE misty_dry_run")


async def _sweep(ctx: Ctx, item_ids, rebuild: bool, cache_only: bool, build_id, job_id,
                 dry_run: bool) -> SweepReport:
    from ..db import tx
    reg = Registry(ctx.con, ctx.clock)
    rep = SweepReport()
    err: Exception | None = None

    # 1. rows: new notes, plus (always) notes that kept a plain mention that a newer registry may now settle
    statuses = ("embedded", "compiled") + (("done", "linked") if rebuild else ())
    q = (f"SELECT * FROM items WHERE note_path IS NOT NULL AND (status IN ({','.join('?' * len(statuses))}) "
         "OR (status = 'done' AND COALESCE(json_extract(meta, '$.plain_n'), 0) > 0))")
    args: list = list(statuses)
    if item_ids is not None:
        q += f" AND id IN ({','.join('?' * len(item_ids))})"
        args += list(item_ids)
    rows = ctx.con.execute(q + " ORDER BY id", args).fetchall()
    rep.items = len(rows)
    if not rows and not ctx.con.execute(
            "SELECT 1 FROM entities WHERE pending=1 AND status IN ('candidate','active') LIMIT 1").fetchone():
        return rep

    ix = AliasIndex.build(reg)
    user = _user_names(ctx)
    generic = _generic_topics(ctx)

    # 2. pass A: every mention against the registry as it stands now (a snapshot: arrival order cannot matter)
    works: list[ItemWork] = []
    for row in rows:
        meta = json.loads(row["meta"] or "{}")
        ms = sorted(mentions_for_item(ctx, row), key=lambda m: (m.role != "counterpart", m.kind, norm(m.raw), m.raw))
        rep.mentions += len(ms)
        decs: list[Decision | None] = [None] * len(ms)
        cp_id = None
        for i, m in enumerate(ms):
            if m.kind == "topic" and norm(m.raw) in generic:
                decs[i] = Decision("rejected", reason="GENERIC")
                continue
            if m.role != "counterpart" and cp_id:
                m = replace(m, ctx=replace(m.ctx, counterpart=cp_id))
                ms[i] = m
            decs[i] = resolve_mention(reg, m, user_names=user, index=ix)
            if m.role == "counterpart" and decs[i].action == "resolved":
                cp_id = decs[i].entity_id
        seen = tuple(sorted({d.entity_id for d in decs if d.action == "resolved"}))
        if seen:
            for i, m in enumerate(ms):
                if decs[i].action in ("judge", "plain"):
                    ms[i] = replace(m, ctx=replace(m.ctx, note_entities=seen))
                    decs[i] = resolve_mention(reg, ms[i], user_names=user, index=ix)
        works.append(ItemWork(row, meta, ms, decs))

    # 3. mention-level questions (one name, several compatible existing entities)
    groups: dict[tuple, dict] = {}
    for w in works:
        for m, d in zip(w.mentions, w.decisions):
            if d.action == "judge":
                k = (m.kind, " ".join(core_tokens(m.raw, m.kind)), d.candidates)
                g = groups.setdefault(k, {"mention": m, "n": 0, "item": w.row})
                g["n"] += 1
    questions: list[Question] = []
    for (kind, key, cands), g in sorted(groups.items()):
        private = privacy.item_is_private(g["item"]["source"], json.loads(g["item"]["meta"] or "{}"),
                                          privacy.private_keys(ctx.settings))
        side = Side(key=f"m:{kind}:{key}", kind=kind, name=g["mention"].raw, aliases=(), line="", n=g["n"],
                    snippet="" if private else _note_summary(ctx, g["item"]["note_path"]))
        for c in cands:
            questions.append(Question(side, _entity_side(ctx, reg, c)))
    verdicts: dict = {}
    if questions:
        try:
            verdicts = await judge_pairs(ctx, questions, job_id=job_id, build_id=build_id, cache_only=cache_only)
        except (CapHit, NetworkDown) as e:
            err, verdicts = e, {}
        rep.judge_pairs += sum(1 for v in verdicts.values() if not v.cached)
        rep.judge_pairs_needed += sum(1 for qq in questions if pair_key(qq.a, qq.b) not in verdicts)

    # 4. settle decisions into final actions; merges implied by "same for several candidates" are queued
    merges_a: list[tuple[str, str]] = []
    pool: list[Occ] = []                        # mentions that are new (or judged apart): cluster them
    plain_pending_flag: set[str] = set()
    for w in works:
        counterpart_core: tuple[str, ...] = ()
        for m, d in zip(w.mentions, w.decisions):
            if m.role == "counterpart" and d.action in ("new", "plain"):
                counterpart_core = core_tokens(m.raw, "person")
        for m, d in zip(w.mentions, w.decisions):
            act, eid = d.action, d.entity_id
            if act == "judge":
                k = (m.kind, " ".join(core_tokens(m.raw, m.kind)), d.candidates)
                same, missing = [], False
                for c in d.candidates:
                    v = verdicts.get(pair_key(Side(f"m:{m.kind}:{k[1]}", m.kind, m.raw, (), "", 0),
                                              Side(c, m.kind, "", (), "", 0)))
                    if v is None:
                        missing = True
                    elif v.verdict == "same":
                        same.append(c)
                if missing and cache_only:
                    # a dry run has no verdicts: assume the strongest candidate (an upper bound for the estimate,
                    # nothing is written because a dry run is rolled back)
                    act, eid = "resolved", min(d.candidates, key=_keep_order(reg))
                elif missing:
                    w.deferred = True
                    act, eid = "deferred", None
                elif same:
                    keep = min(same, key=_keep_order(reg))
                    for other in same:
                        if other != keep:
                            merges_a.append((keep, other))
                    act, eid = "resolved", keep
                else:
                    act = "new" if (m.kind != "person" or len(core_tokens(m.raw, m.kind)) >= 2) else "plain"
            if act == "new" or (act == "plain" and d.reason == "ONE_TOKEN_NO_CANDIDATE"):
                pool.append(Occ(m.item_id, m.raw, m.role, m.ctx, m.kind, counterpart_core, m.origin))
                act = "pool"
            w.final.append((act, eid))
    rep.deferred = sum(1 for w in works if w.deferred)

    # 5. cluster the pool (deterministic: sorted, longest names first)
    clusters = _cluster_pool(pool)
    questions_e: list[Question] = []
    await _attach_similar(ctx, reg, clusters, rep)

    # 6. write: merges, entities, mentions, aliases, handles, item status (one transaction, no awaits)
    with tx(ctx.con):
        for keep, drop in sorted(set(merges_a)):
            ka, da = reg.survivor(keep), reg.survivor(drop)
            if ka != da and not conflict_live(reg, ka, da):
                reg.merge(ka, da, reason="R7", verdict="same")
                rep.merges["R7"] = rep.merges.get("R7", 0) + 1
        known_names, card = _known_context(ctx)
        occ_entity: dict[tuple[str, str, str], str] = {}                   # (item, kind, raw) -> entity id
        for c in clusters:
            if c.attach:
                c.eid = reg.survivor(c.attach)
            else:
                name = c.best_name()
                n = norm(name)
                known = n in known_names or (len(n) >= 4 and f" {n} " in f" {card} ")
                hints = {o.origin for o in c.occs if o.role == "counterpart" and o.origin}
                origin = "contact" if "contact" in hints else "chat" if "chat" in hints else "compile"
                c.eid = reg.new_entity(c.kind, name, known=known or origin == "contact", origin=origin)
                rep.created += 1
                if c.pending:
                    reg.set_pending(c.eid, True)
            for o in c.occs:
                occ_entity[(o.item_id, o.kind, o.raw)] = c.eid
        # one-token mentions attach to a unique new cluster of the batch (or the counterpart's)
        _attach_singles(clusters, pool, occ_entity)
        touched: set[str] = {c.eid for c in clusters if c.eid}
        for w in works:
            old = {(r["entity_id"], r["raw"]) for r in ctx.con.execute(
                "SELECT entity_id, raw FROM mentions WHERE item_id=?", (w.row["id"],))}
            ctx.con.execute("DELETE FROM mentions WHERE item_id=?", (w.row["id"],))
            plain = 0
            cp_entity = None
            for (act, eid), m, d in zip(w.final, w.mentions, w.decisions):
                if act == "deferred":
                    continue                      # waits for a verdict: the item stays unsettled and is swept again
                if act == "rejected" or d.action == "rejected":
                    rep.rejected[d.reason] = rep.rejected.get(d.reason, 0) + 1
                    continue
                if act == "pool":
                    eid = occ_entity.get((m.item_id, m.kind, m.raw))
                    act = "resolved_new" if eid else "plain"
                if act == "plain" or eid is None:
                    plain += 1
                    rep.plain += 1
                    continue
                eid = reg.survivor(eid)
                reg.add_mention(m.item_id, eid, m.raw, m.role)
                if (eid, m.raw) not in old:
                    reg.add_alias(eid, m.raw, n=1)
                if act == "resolved":
                    rep.resolved += 1
                touched.add(eid)
                if m.role == "counterpart":
                    cp_entity = eid
            handle = _counterpart_handle(w.meta)
            if cp_entity and handle:
                reg.add_handle(cp_entity, handle[0], handle[1])
            if w.deferred:
                continue
            meta = dict(w.meta)
            meta["resolved"] = 1
            meta["plain_n"] = plain
            ctx.con.execute("UPDATE items SET status='done', meta=?, updated_at=? WHERE id=?",
                            (json.dumps(meta, sort_keys=True), ctx.clock.now(), w.row["id"]))
            rep.settled.append(w.row["id"])
        for e in touched:
            reg.recompute_name(e)
        # names of entities touched may have changed: pairs we already know are judged by id, so nothing else here
        touched = {reg.survivor(e) for e in touched}

    if not dry_run and rep.settled:
        relink_settled(ctx, rep.settled)          # notes link only to pages that exist (plain names otherwise)

    # 7-8. entity-level questions: new clusters near an existing entity or each other, and pending leftovers.
    # A merge gives the survivor new spellings, so its neighbourhood changes: ask again, a few rounds at most.
    pending_ids = {r["id"] for r in ctx.con.execute(
        "SELECT id FROM entities WHERE pending=1 AND status IN ('candidate','active')")}
    scope = sorted(touched | pending_ids | ({r["id"] for r in reg.entities()} if rebuild else set()))
    seen_scope = set(scope)
    unsure_all: set[str] = set()
    for _round in range(ROUNDS):
        ix2 = AliasIndex.build(reg)
        qs_e, autos = _entity_questions(ctx, reg, ix2, scope)
        verdicts_e: dict = {}
        if qs_e:
            try:
                verdicts_e = await judge_pairs(ctx, qs_e, job_id=job_id, build_id=build_id, cache_only=cache_only)
            except (CapHit, NetworkDown) as e:
                err = err or e
            rep.judge_pairs += sum(1 for v in verdicts_e.values() if not v.cached)
            rep.judge_pairs_needed += sum(1 for qq in qs_e if pair_key(qq.a, qq.b) not in verdicts_e)
        merged_before = sum(rep.merges.values())
        changed: set[str] = set()
        with tx(ctx.con):
            for a, b, why in autos:                                # R1 (one full name) and R6 (same name, same words)
                a, b = reg.survivor(a), reg.survivor(b)
                if a != b and not conflict_live(reg, a, b):
                    keep, drop = sorted((a, b), key=_keep_order(reg))
                    reg.merge(keep, drop, reason=why)
                    rep.merges[why] = rep.merges.get(why, 0) + 1
                    changed.add(keep)
            same: list[tuple[str, str]] = []
            for qq in sorted(qs_e, key=lambda x: pair_key(x.a, x.b)):
                a, b = reg.survivor(qq.a.key), reg.survivor(qq.b.key)
                if a == b:
                    continue
                v = verdicts_e.get(pair_key(qq.a, qq.b))
                if v is None or v.verdict == "unsure":
                    unsure_all |= {a, b}
                elif v.verdict == "same":
                    same.append((a, b))
            changed |= _merge_same(reg, same, unsure_all, rep)
        if err is not None or sum(rep.merges.values()) == merged_before:
            break
        scope = sorted({reg.survivor(e) for e in changed})            # the survivors' new spellings: ask again
        seen_scope |= set(scope)
        if not scope:
            break
    with tx(ctx.con):
        # pending = still in an open question; everything else in scope is settled (an entity answered "different"
        # or merged in a later round is no longer pending, one asked and unanswered stays)
        open_ids = {reg.survivor(e) for e in unsure_all}
        for eid in seen_scope:
            s_ = reg.survivor(eid)
            e = reg.get(s_)
            if e is None or e["status"] in ("merged", "dropped"):
                continue
            if bool(e["pending"]) != (s_ in open_ids):
                reg.set_pending(s_, s_ in open_ids)
        rep.pending = ctx.con.execute(
            "SELECT COUNT(*) FROM entities WHERE pending=1 AND status IN ('candidate','active')").fetchone()[0]
    with tx(ctx.con):
        if not rep.deferred:                     # an entity whose item is still waiting is not an orphan
            _drop_orphans(ctx, reg)
    scope = sorted(seen_scope)
    rep.qualifying = sorted({s for s in (reg.survivor(e) for e in scope)
                             if (reg.get(s) is not None and reg.get(s)["status"] == "candidate" and qualifies(ctx, s))})
    log_event("SWEEP", count=rep.items, mentions=rep.mentions, resolved=rep.resolved, created=rep.created,
              plain=rep.plain, merged=sum(rep.merges.values()), pending=rep.pending, pairs=rep.judge_pairs,
              needed=rep.judge_pairs_needed, qualifying=len(rep.qualifying))
    if err is not None:
        raise err
    return rep


def _user_names(ctx: Ctx) -> frozenset[str]:
    n = norm(str(ctx.settings.get("user_name") or ""))
    return frozenset({n, n.split()[0]}) if n else frozenset()


def _drop_orphans(ctx: Ctx, reg: Registry) -> None:
    """A candidate that lost every mention (a recompile changed the names) is dropped, never left as a ghost."""
    for r in ctx.con.execute(
            "SELECT e.id FROM entities e WHERE e.status='candidate' AND e.known=0 AND e.origin NOT IN ('contact','user') "
            "AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.entity_id = e.id)").fetchall():
        reg.set_status(r["id"], "dropped")


# ------------------------------------------------------------------ clustering

def _cluster_pool(pool: list[Occ]) -> list[Cluster]:
    """Group new mentions into clusters. People: greedy clique over multi-token names, longest and most-mentioned
    first, so the grouping depends on the names, not on the order they arrived. Others: exact name."""
    clusters: list[Cluster] = []
    people: dict[tuple, list[Occ]] = {}
    other: dict[tuple[str, str], list[Occ]] = {}
    for o in pool:
        if o.kind == "person":
            core = core_tokens(o.raw, "person")
            if len(core) >= 2 or o.role == "counterpart":
                people.setdefault(core, []).append(o)
        else:
            other.setdefault((o.kind, " ".join(core_tokens(o.raw, o.kind))), []).append(o)
    for t in sorted(people, key=lambda t: (-len(t), -len(people[t]), t)):
        for c in clusters:
            if c.kind == "person" and all(compatible(t, m) for m in c.members):
                c.members[t] = people[t]
                break
        else:
            clusters.append(Cluster("person", {t: people[t]}))
    for (kind, key), occs in sorted(other.items()):
        clusters.append(Cluster(kind, {tuple(key.split()): occs}))
    return clusters


def _attach_singles(clusters: list[Cluster], pool: list[Occ], occ_entity: dict) -> None:
    people = [c for c in clusters if c.kind == "person" and c.eid]
    for o in pool:
        if o.kind != "person" or (o.item_id, o.kind, o.raw) in occ_entity:
            continue
        core = core_tokens(o.raw, "person")
        if len(core) != 1:
            continue
        hits = [c for c in people if any(compatible(core, t) for t in c.members)]
        if len(hits) > 1 and o.cp_core:
            hits = [c for c in hits if any(compatible(o.cp_core, t) or t == o.cp_core for t in c.members)]
        if len(hits) == 1:
            occ_entity[(o.item_id, o.kind, o.raw)] = hits[0].eid


async def _attach_similar(ctx: Ctx, reg: Registry, clusters: list[Cluster], rep: SweepReport) -> None:
    """R6 for projects and topics: near-identical names fold into one cluster or an existing entity; the band in
    between becomes a pending entity plus a question (asked in step 7 by id)."""
    for kind in ("project", "topic"):
        cl = [c for c in clusters if c.kind == kind]
        if not cl:
            continue
        for c in cl:
            c.text = c.best_name()
        existing = _ensure_vecs(ctx, reg, kind)
        items_of = _items_by_entity(ctx)
        mat = await asyncio.to_thread(ctx.embedder.embed_documents, [c.text for c in cl])
        keep: list[tuple[Cluster, np.ndarray]] = []
        for c, v in zip(cl, mat):
            c_tokens = {t for t in " ".join(core_tokens(c.best_name(), kind)).split()}
            best_id, best_cos, best_j = None, -1.0, 0.0
            c_items = {o.item_id for o in c.occs}
            for eid, ev in sorted(existing.items()):
                cos = float(np.dot(v, ev))
                if cos > best_cos:
                    best_id, best_cos = eid, cos
                    best_j = max((_jaccard(c_tokens, s) for s in _alias_token_sets(reg, eid)), default=0.0)
            together = bool(best_id and c_items & items_of.get(best_id, set()))
            same_marks = bool(best_id) and _same_marks(c_tokens, set().union(*_alias_token_sets(reg, best_id)))
            if best_id and best_cos >= _t(ctx, "auto_cos") and best_j >= _t(ctx, "auto_jaccard") and not together and same_marks:
                c.attach = best_id
                rep.merges["R6"] = rep.merges.get("R6", 0) + 1
                continue
            folded = False
            for k, kv in keep:
                k_tokens = {t for t in " ".join(core_tokens(k.best_name(), kind)).split()}
                if c_items & {o.item_id for o in k.occs}:
                    continue                      # named together in one note: let the judge say, never fold blindly
                if (float(np.dot(v, kv)) >= _t(ctx, "auto_cos") and _jaccard(c_tokens, k_tokens) >= _t(ctx, "auto_jaccard")
                        and _same_marks(c_tokens, k_tokens)):
                    for t, o in c.members.items():
                        k.members.setdefault(t, []).extend(o)
                    c.members = {}
                    folded = True
                    break
            if not folded:
                keep.append((c, v))
        clusters[:] = [c for c in clusters if c.members or c.attach]
        # the similarity band is handled by id after creation (step 7), via _entity_questions


def cut_bridges(reg: Registry, edges: list[tuple[str, str]], unsure: set[str] | None = None) -> list[tuple[str, str]]:
    """Drop "same" edges that would bridge two people who conflict. "Pranav Naveen Asrani" fits both "Pranav
    Asrani" and "Naveen Asrani": it keeps the one it shares a first name with, and neither when that does not
    decide it. Returns the surviving edges in a name-sorted, data-only order."""
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    def firsts(e: str) -> set[str]:
        return {t[0] for t in reg.alias_tokens(e) if t}

    def clash(ids) -> bool:
        ids = sorted(ids)
        return any(conflict_live(reg, x, y) for i, x in enumerate(ids) for y in ids[i + 1:])

    for x in sorted(adj):
        if len(adj[x]) > 1 and clash(adj[x]):
            keep = {t for t in adj[x] if firsts(x) & firsts(t)}
            if clash(keep):
                keep = set()
            for t in sorted(adj[x] - keep):
                adj[x].discard(t)
                adj[t].discard(x)
                if unsure is not None:
                    unsure |= {x, t}
    name = lambda e: (reg.get(e)["name"].lower(), e)          # noqa: E731
    pairs = {tuple(sorted((a, b), key=name)) for a, ns in adj.items() for b in ns}
    return sorted(pairs, key=lambda p: (name(p[0]), name(p[1])))


def _merge_same(reg: Registry, edges: list[tuple[str, str]], unsure: set[str], rep: SweepReport) -> set[str]:
    """Apply "same" verdicts: bridges cut, the rest merged in order, each checked against the registry as it is.
    Returns the survivors (their spellings changed)."""
    kept: set[str] = set()
    for a, b in cut_bridges(reg, edges, unsure):
        a, b = reg.survivor(a), reg.survivor(b)
        if a != b and not conflict_live(reg, a, b):
            keep, drop = sorted((a, b), key=_keep_order(reg))
            reg.merge(keep, drop, reason="R7", verdict="same")
            rep.merges["R7"] = rep.merges.get("R7", 0) + 1
            kept.add(keep)
            if drop in unsure:
                unsure.discard(drop)
                unsure.add(keep)
    return kept


def _keep_order(reg: Registry):
    """Which of several entities survives a merge: one with a page, then one with a real summary, then more
    sources, then the older, then the name (never the random id, so the outcome is repeatable)."""
    def key(e: str):
        t = reg.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (e,)).fetchone()
        has = bool(t and (t["summary"] or "").strip() not in ("", "(stub)"))
        r = reg.get(e)
        return (r["status"] != "active", not has, -reg.mention_count(e), r["created_at"], r["name"].lower(), e)
    return key


def _entity_questions(ctx: Ctx, reg: Registry, ix: AliasIndex, scope: list[str]):
    """Pairs of live entities that may be one thing and are not yet settled.
    Returns (questions for the judge, automatic merges [(a, b, reason)]).
    People: one shared full-name alias is one person (R1); compatible but different names go to the judge;
    conflicting names never do. Projects and topics: near-identical (R6) merge, the band goes to the judge."""
    qs: dict[str, Question] = {}
    autos: dict[str, tuple[str, str, str]] = {}
    by_kind: dict[str, list[str]] = {}
    for e in reg.entities():
        by_kind.setdefault(e["kind"], []).append(e["id"])
    vecs_by_kind: dict[str, dict[str, np.ndarray]] = {}
    items_of = _items_by_entity(ctx)
    toks: dict[str, set[str]] = {}

    def tokset(eid: str) -> set[str]:
        if eid not in toks:
            toks[eid] = {t for ts in reg.alias_tokens(eid) for t in ts}
        return toks[eid]

    for eid in scope:
        e = reg.get(eid)
        if e is None or e["status"] not in ("candidate", "active"):
            continue
        if e["kind"] == "person":
            mine = {" ".join(t) for t in ix.tokens.get(eid, []) if len(t) >= 2}
            for other in by_kind.get("person", []):
                if other == eid:
                    continue
                k = "|".join(sorted((eid, other)))
                if k in qs or k in autos:
                    continue
                theirs = {" ".join(t) for t in ix.tokens.get(other, []) if len(t) >= 2}
                if mine & theirs:
                    autos[k] = (eid, other, "R1")
                elif entities_compatible(ix, eid, other) and not entities_conflict(ix, eid, other):
                    qs[k] = Question(_entity_side(ctx, reg, eid), _entity_side(ctx, reg, other))
        elif e["kind"] in ("project", "topic"):
            vecs = vecs_by_kind.get(e["kind"])
            if vecs is None:
                vecs = vecs_by_kind[e["kind"]] = _ensure_vecs(ctx, reg, e["kind"])
            v = vecs.get(eid)
            if v is None:
                continue
            mine_items = items_of.get(eid, set())
            near: list[tuple[float, str]] = []
            for other, ov in vecs.items():
                if other == eid:
                    continue
                cos = float(np.dot(v, ov))
                if cos < _t(ctx, "judge_cos"):
                    continue
                if not (_words(tokset(eid)) & _words(tokset(other))) and cos < NEAR_IDENTICAL_COS:
                    continue                        # no word in common and not near-identical: not worth a question
                near.append((cos, other))
            near.sort(key=lambda t: (-t[0], t[1]))
            for cos, other in near[:TOP_K]:
                k = "|".join(sorted((eid, other)))
                if k in qs or k in autos:
                    continue
                together = bool(mine_items & items_of.get(other, set()))     # two spellings in one note: ask, don't fold
                if (cos >= _t(ctx, "auto_cos") and _jaccard(tokset(eid), tokset(other)) >= _t(ctx, "auto_jaccard")
                        and not together
                        and _same_marks(tokset(eid), tokset(other))):
                    autos[k] = (eid, other, "R6")
                else:
                    qs[k] = Question(_entity_side(ctx, reg, eid), _entity_side(ctx, reg, other))
    return [qs[k] for k in sorted(qs)], [autos[k] for k in sorted(autos)]


def relink_settled(ctx: Ctx, item_ids: list[str]) -> int:
    """Point the People and Links of freshly resolved notes at pages that exist (plain names otherwise)."""
    from . import render
    if not item_ids or render.legacy_pages(ctx):
        return 0
    n = 0
    with ctx.wiki.batch("resolve: relink notes"):
        for iid in item_ids:
            r = ctx.con.execute("SELECT note_path FROM items WHERE id=?", (iid,)).fetchone()
            if r and r["note_path"] and render.relink_note(ctx, r["note_path"], iid):
                n += 1
    return n


@handler("resolve")
async def resolve_handler(job: Job, ctx: Ctx) -> None:
    from ..pipeline import REGISTRY
    rep = await resolve_sweep(ctx, job_id=job.id, build_id=job.payload.get("build_id"))
    if rep.qualifying and "summarise" in REGISTRY and not ctx.queue.pending(("summarise",)):
        ctx.queue.enqueue("summarise", payload={}, cloud=True)       # the page waits for its summary

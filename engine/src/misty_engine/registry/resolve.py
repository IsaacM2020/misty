"""Deterministic resolver, stages R0 to R5 (spec 3.2). Pure decisions over the registry: it never writes.

`resolve_mention` answers "what is this mention?" with one of: resolved (an existing entity), new (a thing we
have not seen), judge (a question for the R7 judge), plain (stays text, no entity), rejected (not a thing).
Stage R6 (embedding similarity for projects and topics) and R7 live in the sweep, which sees whole batches."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .names import compatible, core_tokens, reject_reason
from .store import Registry


@dataclass(frozen=True)
class MentionCtx:
    source: str = ""
    chat_id: str = ""
    chat_title: str = ""
    chat_kind: str = ""                              # "1to1" | "group" | ""
    counterpart: str | None = None                   # entity id of the 1:1 chat's other person
    sender_handle: tuple[str, str] | None = None     # ("email"|"phone"|"jid", value)
    note_entities: tuple[str, ...] = ()              # entity ids already resolved in the same note


@dataclass(frozen=True)
class Mention:
    item_id: str
    kind: str
    raw: str
    role: str = "named"
    ctx: MentionCtx = field(default_factory=MentionCtx)
    origin: str = ""                      # "contact" | "chat": where the name came from, when it is not the compiler


@dataclass(frozen=True)
class Decision:
    action: str                       # resolved | new | judge | plain | rejected
    entity_id: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""


class AliasIndex:
    """Snapshot of the live aliases, taken once per sweep so thousands of mentions do not rescan the table.
    Resolution in a sweep always answers against the registry as it was when the sweep began."""

    def __init__(self):
        self.by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.by_token: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.tokens: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        self.kind: dict[str, str] = {}

    @classmethod
    def build(cls, reg: Registry) -> "AliasIndex":
        ix = cls()
        rows = reg.con.execute(
            "SELECT a.entity_id, a.kind, a.alias_norm, e.created_at FROM entity_aliases a "
            "JOIN entities e ON e.id = a.entity_id WHERE e.status IN ('candidate','active') "
            "ORDER BY e.created_at, a.entity_id, a.alias_norm").fetchall()
        for r in rows:
            eid, kind, key = r["entity_id"], r["kind"], r["alias_norm"]
            ix.kind[eid] = kind
            if eid not in ix.by_key[(kind, key)]:
                ix.by_key[(kind, key)].append(eid)
            toks = tuple(key.split())
            ix.tokens[eid].append(toks)
            for t in toks:
                ix.by_token[(kind, t)].add(eid)
        return ix

    def find(self, kind: str, key: str) -> list[str]:
        return list(self.by_key.get((kind, key), ()))

    def live(self, eid: str, kind: str) -> bool:
        return self.kind.get(eid) == kind

    def person_candidates(self, core: tuple[str, ...]) -> list[str]:
        pool: set[str] = set()
        for t in set(core):
            pool |= self.by_token.get(("person", t), set())
        return sorted(e for e in pool if any(compatible(core, t) for t in self.tokens[e]))


def entities_compatible(ix: AliasIndex, a: str, b: str) -> bool:
    """Do two entities look like one person? Compared by their multi-token names when they have them, so a
    shared one-token alias such as "pranav" never makes Pranav Asrani and Pranav Kumar compatible."""
    ta, tb = ix.tokens.get(a, []), ix.tokens.get(b, [])
    ma = [t for t in ta if len(t) >= 2] or ta
    mb = [t for t in tb if len(t) >= 2] or tb
    return any(compatible(x, y) for x in ma for y in mb)


def entities_conflict(ix: AliasIndex, a: str, b: str) -> bool:
    """Both have full names and some pair of them cannot be one person ("Pranav Asrani" vs "Naveen Asrani", even
    when "Pranav Naveen Asrani" fits both). Conflicting entities are never merged, whatever a judge says."""
    if ix.kind.get(a) != "person" or ix.kind.get(b) != "person":
        return False
    ma = [t for t in ix.tokens.get(a, []) if len(t) >= 2]
    mb = [t for t in ix.tokens.get(b, []) if len(t) >= 2]
    return any(not compatible(x, y) for x in ma for y in mb)


def conflict_live(reg: Registry, a: str, b: str) -> bool:
    """entities_conflict against the registry as it is right now (aliases learned by earlier merges count)."""
    ea, eb = reg.get(a), reg.get(b)
    if ea is None or eb is None or ea["kind"] != "person" or eb["kind"] != "person":
        return False
    ma = [t for t in reg.alias_tokens(a) if len(t) >= 2]
    mb = [t for t in reg.alias_tokens(b) if len(t) >= 2]
    return any(not compatible(x, y) for x in ma for y in mb)


def _mutually_compatible(ix: AliasIndex, ids: list[str]) -> bool:
    return all(entities_compatible(ix, a, b) and not entities_conflict(ix, a, b)
               for i, a in enumerate(ids) for b in ids[i + 1:])


def _others(reg: Registry, eid: str, item_id: str) -> int:
    return reg.con.execute("SELECT COUNT(DISTINCT item_id) FROM mentions WHERE entity_id=? AND item_id!=?",
                           (eid, item_id)).fetchone()[0]


def evidence(reg: Registry, eid: str, m: Mention) -> int:
    """Score for "this mention is probably that entity": 2 for the same chat seen before, 2 for the same sender,
    1 for each entity already resolved in the same note that also shares a note with it. The mention's own
    item never counts as evidence for itself, so re-running a sweep cannot change an answer."""
    c, ctx = reg.con, m.ctx
    score = 0
    if ctx.chat_id:
        q = ("SELECT 1 FROM mentions x JOIN items i ON i.id = x.item_id WHERE x.entity_id=? AND x.item_id!=? "
             "AND json_extract(i.meta,'$.chat_id')=?")
        args: list = [eid, m.item_id, ctx.chat_id]
        if ctx.source:
            q += " AND i.source=?"
            args.append(ctx.source)
        if c.execute(q + " LIMIT 1", args).fetchone():
            score += 2
    if ctx.sender_handle:
        if c.execute("SELECT 1 FROM mentions x JOIN items i ON i.id = x.item_id WHERE x.entity_id=? "
                     "AND x.item_id!=? AND json_extract(i.meta,'$.sender')=? LIMIT 1",
                     (eid, m.item_id, ctx.sender_handle[1])).fetchone():
            score += 2
    for other in ctx.note_entities:
        if other != eid and c.execute(
                "SELECT 1 FROM mentions a JOIN mentions b ON a.item_id = b.item_id "
                "WHERE a.entity_id=? AND b.entity_id=? AND a.item_id!=? LIMIT 1",
                (eid, other, m.item_id)).fetchone():
            score += 1
    return score


def resolve_mention(reg: Registry, m: Mention, *, user_names: frozenset[str] = frozenset(),
                    index: AliasIndex | None = None) -> Decision:
    ix = index if index is not None else AliasIndex.build(reg)
    ctx = m.ctx
    code = reject_reason(m.raw, m.kind, chat_title=ctx.chat_title, chat_kind=ctx.chat_kind, user_names=user_names)
    if code:
        return Decision("rejected", reason=code)
    core = core_tokens(m.raw, m.kind)
    key = " ".join(core)
    ids = ix.find(m.kind, key)

    if m.kind != "person":
        if len(ids) == 1:
            return Decision("resolved", ids[0], reason="R1")
        if len(ids) > 1:
            return Decision("judge", candidates=tuple(ids), reason="DUP_ALIAS")
        return Decision("new", reason="NEEDS_SIMILARITY")

    if len(core) >= 2 and len(ids) == 1:                                                       # R1
        return Decision("resolved", ids[0], reason="R1")

    if ctx.sender_handle:                                                                       # R2
        eid = reg.by_handle(*ctx.sender_handle)
        if eid and ix.live(eid, "person") and any(compatible(core, t) for t in ix.tokens[eid]):
            return Decision("resolved", eid, reason="R2")

    if ctx.chat_kind == "1to1" and ctx.counterpart:                                             # R3
        cp = reg.survivor(ctx.counterpart)
        if ix.live(cp, "person") and any(compatible(core, t) for t in ix.tokens[cp]):
            return Decision("resolved", cp, reason="R3")

    cands = ix.person_candidates(core)                                                          # R4
    if not cands:
        if len(core) >= 2 or m.role == "counterpart":     # the chat itself says this is a person with this name
            return Decision("new", reason="NEW")
        return Decision("plain", reason="ONE_TOKEN_NO_CANDIDATE")
    if len(cands) == 1:
        c = cands[0]
        if len(core) >= 2:
            return Decision("resolved", c, reason="R4")
        e = reg.get(c)
        if e["known"] or e["origin"] in ("contact", "user") or (e["origin"] == "migration" and e["status"] == "active"):
            return Decision("resolved", c, reason="R4_KNOWN")  # a one-word name that is known ("Mom") or already had a page
        if evidence(reg, c, m) >= 1 or _others(reg, c, m.item_id) >= 2:
            return Decision("resolved", c, reason="R4_UNIQUE")
        return Decision("plain", reason="ONE_TOKEN_NO_EVIDENCE")
    if _mutually_compatible(ix, cands):
        return Decision("judge", candidates=tuple(cands), reason="CLASS")

    scored = sorted(((evidence(reg, c, m), c) for c in cands), key=lambda t: (-t[0], t[1]))      # R5
    best = scored[0][0]
    leaders = [c for s, c in scored if best >= 2 and 2 * s > best]
    if best >= 2 and len(leaders) == 1:
        return Decision("resolved", leaders[0], reason="R5")
    if len(leaders) >= 2 and _mutually_compatible(ix, leaders):
        return Decision("judge", candidates=tuple(sorted(leaders)), reason="CLASS")
    if len(core) >= 2:
        return Decision("judge", candidates=tuple(sorted(c for _, c in scored[:2])), reason="AMBIGUOUS")
    return Decision("plain", reason="ONE_TOKEN_AMBIGUOUS")

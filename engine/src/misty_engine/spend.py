"""Cost meter and caps (spec section 10)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .clock import Clock, sgt_day_start
from .config import Settings
from .types import Phase

# areas, about and priorities are one family of rare "core" jobs and share one sub-cap
PURPOSE_GROUPS: dict[str, tuple[str, ...]] = {"core": ("core", "areas", "about", "priorities")}
_GROUP_OF = {m: g for g, ms in PURPOSE_GROUPS.items() for m in ms}


def estimate_usd(pinned, model_key: Literal["jev", "haiku", "strong", "cheap"], in_tokens: int, out_tokens: int,
                 cached_tokens: int = 0) -> float:
    p = pinned.prices
    pin = getattr(p, f"{model_key}_in", 0.0)
    pout = getattr(p, f"{model_key}_out", 0.0)
    pcache = getattr(p, f"{model_key}_cache_read", pin)
    fresh = max(in_tokens - cached_tokens, 0)
    return (fresh * pin + cached_tokens * pcache + out_tokens * pout) / 1_000_000


@dataclass(frozen=True)
class CapDecision:
    ok: bool
    spent: float
    cap: float
    reason: str | None = None


class SpendMeter:
    def __init__(self, con, clock: Clock, settings: Settings):
        self.con, self.clock, self.settings = con, clock, settings

    def record(self, *, provider: str, model: str, job_id: str | None, build_id: str | None, usd: float,
               purpose: str | None = None) -> None:
        self.con.execute(
            "INSERT INTO spend(ts, provider, model, job_id, build_id, usd, purpose) VALUES (?,?,?,?,?,?,?)",
            (self.clock.now(), provider, model, job_id, build_id, float(usd), purpose))

    def spent_today(self) -> float:
        start = sgt_day_start(self.clock.now())
        r = self.con.execute(
            "SELECT COALESCE(SUM(usd),0) FROM spend WHERE build_id IS NULL AND ts>=?", (start,)).fetchone()
        return float(r[0])

    def spent_build(self, build_id: str | None = None) -> float:
        if build_id:
            r = self.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend WHERE build_id=?", (build_id,)).fetchone()
        else:
            r = self.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend WHERE build_id IS NOT NULL").fetchone()
        return float(r[0])

    def spent_purpose(self, purpose: str, phase: Phase, build_id: str | None = None) -> float:
        """Spend of one purpose (a group counts as one), today for live, this build for a build."""
        group = _GROUP_OF.get(purpose, purpose)
        members = PURPOSE_GROUPS.get(group, (group,))
        marks = ",".join("?" * len(members))
        if phase == "build":
            sql = f"SELECT COALESCE(SUM(usd),0) FROM spend WHERE purpose IN ({marks}) AND build_id IS NOT NULL"
            args: list = list(members)
            if build_id:
                sql += " AND build_id=?"
                args.append(build_id)
        else:
            sql = f"SELECT COALESCE(SUM(usd),0) FROM spend WHERE purpose IN ({marks}) AND build_id IS NULL AND ts>=?"
            args = [*members, sgt_day_start(self.clock.now())]
        return float(self.con.execute(sql, args).fetchone()[0])

    def purpose_cap(self, purpose: str | None, phase: Phase) -> tuple[str, float] | None:
        """(group, usd cap) for a purpose that has a sub-cap, else None."""
        if not purpose:
            return None
        group = _GROUP_OF.get(purpose, purpose)
        if phase == "build":
            frac = (self.settings.get("purpose_caps_build_frac") or {}).get(group)
            return None if frac is None else (group, float(frac) * float(self.settings.get("cap_build_usd")))
        cap = (self.settings.get("purpose_caps_daily") or {}).get(group)
        return None if cap is None else (group, float(cap))

    def check(self, estimate: float, phase: Phase, build_id: str | None = None,
              purpose: str | None = None) -> CapDecision:
        if phase == "build":
            cap, spent, reason = float(self.settings.get("cap_build_usd")), self.spent_build(build_id), "CAP_BUILD"
        else:
            cap, spent, reason = float(self.settings.get("cap_daily_usd")), self.spent_today(), "CAP_DAILY"
        if spent + estimate > cap + 1e-12:
            return CapDecision(False, spent, cap, reason)
        sub = self.purpose_cap(purpose, phase)
        if sub is not None:
            group, pcap = sub
            pspent = self.spent_purpose(group, phase, build_id)
            if pspent + estimate > pcap + 1e-12:
                return CapDecision(False, pspent, pcap, f"CAP_PURPOSE:{group}")
        return CapDecision(True, spent, cap, None)

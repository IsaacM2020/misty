"""Pinned external facts (config/pinned.toml) and runtime settings (db key-value)."""
from __future__ import annotations

import json
import os
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OpenRouterCfg:
    base_url: str
    chat_path: str
    systemone_path: str
    referer: str
    title: str


@dataclass(frozen=True)
class ModelsCfg:
    jev: str
    haiku: str
    haiku_provider_order: tuple[str, ...]
    haiku_min_cache_tokens: int
    strong: str = ""
    strong_provider_order: tuple[str, ...] = ()
    answer_cheap: str = ""
    cheap_provider_order: tuple[str, ...] = ()
    # the `reasoning` request body per model key; Opus on the ZDR route refuses `enabled: false` (docs/verify/models-q.md)
    haiku_reasoning: dict = field(default_factory=lambda: {"enabled": False})
    strong_reasoning: dict = field(default_factory=lambda: {"effort": "low"})
    cheap_reasoning: dict = field(default_factory=lambda: {"enabled": False})


@dataclass(frozen=True)
class PricesCfg:
    jev_in: float
    jev_out: float
    haiku_in: float
    haiku_out: float
    haiku_cache_read: float
    haiku_cache_write: float
    strong_in: float = 0.0
    strong_out: float = 0.0
    strong_cache_read: float = 0.0
    cheap_in: float = 0.0
    cheap_out: float = 0.0


@dataclass(frozen=True)
class JevCfg:
    max_input_tokens: int
    confidence_keep_below: float
    multi_select_threshold: float


@dataclass(frozen=True)
class EmbeddingCfg:
    model_id: str
    revision: str
    backend: str
    dim: int
    batch: int
    max_len: int
    query_prefix: str
    doc_prefix: str
    image_support: bool
    source_model: str = ""
    mlx_vlm_commit: str = ""


@dataclass(frozen=True)
class ResolveCfg:
    """Entity resolution thresholds (spec 3.2). Calibrated on the real names in Q5."""
    auto_cos: float = 0.92
    judge_cos: float = 0.80
    auto_jaccard: float = 0.5
    min_sources: int = 2


@dataclass(frozen=True)
class Pinned:
    openrouter: OpenRouterCfg
    models: ModelsCfg
    prices: PricesCfg
    jev: JevCfg
    embedding: EmbeddingCfg
    sqlite_vec_version: str
    force_graph: dict[str, str] = field(default_factory=dict)
    resolve: ResolveCfg = field(default_factory=ResolveCfg)

    @property
    def prices_(self) -> PricesCfg:  # readability alias
        return self.prices


def default_pinned_path() -> Path:
    env = os.environ.get("MISTY_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config" / "pinned.toml"


def load_pinned(path: Path | None = None) -> Pinned:
    d = tomllib.loads((path or default_pinned_path()).read_text())
    m = dict(d["models"])
    for k in ("haiku_provider_order", "strong_provider_order", "cheap_provider_order"):
        if k in m:
            m[k] = tuple(m[k])
    return Pinned(
        openrouter=OpenRouterCfg(**d["openrouter"]),
        models=ModelsCfg(**m),
        prices=PricesCfg(**d["prices"]),
        jev=JevCfg(**d["jev"]),
        embedding=EmbeddingCfg(**d["embedding"]),
        sqlite_vec_version=d["sqlite_vec"]["version"],
        force_graph=dict(d.get("force_graph", {})),
        resolve=ResolveCfg(**d.get("resolve", {})),
    )


DEFAULTS: dict[str, Any] = {
    "cap_build_usd": 3.0,
    "cap_daily_usd": 0.20,
    "chat_tail_in_user": False,           # cost work: per-message context (clock, notes, open window) rides in front of his line so the history stays cached. Off until the chat evals are run with it on
    "activity_trail": True,               # the app/window/doc trail (activity.py); off stops writing
    "collect_phrases": False,             # F6: keep the words of what the mic heard, on this Mac, 14 days (phrases.py); off by default
    "private_apps": None,                # [{name, bundle}] she must not look at; None = not seeded yet (privacy.seed_defaults)
    # per-purpose sub-caps (spec 3.11). A purpose over its sub-cap waits (waiting_cap) while everything else runs.
    "purpose_caps_daily": {"judge": 0.01, "summarise": 0.03, "ask": 0.05, "core": 0.02, "teach": 0.10},
    "purpose_caps_build_frac": {"judge": 0.10, "summarise": 0.25, "core": 0.10, "eval": 0.083},
    "class_reach": {"handled": []},   # calendar classes already asked about or auto-started (never twice for one event)
    # Saying "Edith" starts the voice session the hotkey starts (on-device keyword spotter in the app: Core/Voice/WakeWord.swift).
    # sensitivity 0..1: higher wakes on a fainter "Edith" and false-wakes more (0.5 is tuned on his recorded hours).
    "wake_word": {"enabled": True, "word": "edith", "sensitivity": 0.5},
    "speak_up": {"cap": 5, "quiet_from": 23, "quiet_to": 7},   # the gate: told a day at most, silent between these SGT hours
    # which model each background job uses: "haiku" (default) or "cheap" (answer_cheap). Flip one only after its eval passes
    # (see docs/job-cost-evals.md).
    "job_models": {"compile": "haiku", "summarise": "haiku", "judge": "haiku"},
    # Background jobs on the Mac Mini (local_llm.py). Off until Isaac turns it on: set enabled true. `purposes` is an allow-list;
    # chat, stream, addressed, jev, tutor, lesson, brief, class and the voice path can never go local (local_llm.NEVER_LOCAL).
    # Any local failure falls back to the cloud for that request; 3 in a row pause local for 10 minutes.
    "local_llm": {"enabled": False, "url": "http://127.0.0.1:8080", "model": "inkling-2-mini",
                  "purposes": ["summarise", "compile", "chat_compact"], "timeout_s": 120, "ctx": 16384},
    "live_compile_min_items": 6,          # live compile waits for this many items...
    "live_compile_max_wait_min": 30,      # ...or until the oldest has waited this long, so calls are fewer and fuller
    "allow_no_zdr": False,                # zero data retention is required; only flip this yourself, knowingly
    "recompile_cooldown_h": 24,           # a note already written waits this long before a changed file is compiled again
                                          # (session-log.md alone was recompiled 116 times in a week, one Haiku call each)
    "paused": False,
    "paid_calls_allowed": False,
    "phase": "build",
    "synth_done": False,          # stage 5 has run once; later builds go straight back to live
    "area_gaps": [],                      # what the area synthesis says it could not see (missing sources)
    "user_name": "",                      # shown at the centre of the graph; empty = "Me". Also fills <<name>> in her prompts.
    "user_curriculum": "",                # his exam programme ("IB"): fills <<curriculum>> in the tutoring rules; empty = left out
    "user_line": "",                      # one line about him ("Sam is 16, a student in Leeds."): fills <<about>> in her prompts
    # NOTE: the spec's "iCloud Drive Archive" does not exist on this Mac (checked 2026-10-14; only
    # ~/Library/Mobile Documents/com~apple~CloudDocs/Obsidian exists). Add its path here if it ever appears.
    "folders": ["~/Desktop", "~/Downloads", "~/Notes", "~/Code", "~/Projects", "~/Pictures"],
    "excluded_paths": [
        "~/Code/Water/oldapp", "~/Code/_archive", "~/Old Knowledge Base",
        "~/oldproject", "~/Library", "~/Misty",
    ],
    "sources_enabled": {
        "folders": True, "repos": True, "claude_mem": True, "graphify": True,
        "gmail": False, "gcal": False, "imessage": False, "whatsapp": False,
    },
    "about_pinned": False,                # a card the person pinned is never rewritten
    "about_card_at": None,                # when the card was last written, and how many notes existed then
    "about_card_notes": 0,
    "summary_strong_top_pct": 0,          # share of entities (by importance) summarised on the strong model; 0 = none
    "context_private_chats": [],          # "whatsapp:<chat_id>" keys kept out of context packs (spec 3.10)
    "imessage_excluded": [],
    "whatsapp_picked": [],
    "whatsapp_exports": [],
    "shortcut_allow": [],                 # D2: names of his Shortcuts she may run, built by talking (never from text she read)
    "gcal_calendars": ["primary"],        # P20: primary only in V1
    "gmail_months": 18,
    "mail_allow_domains": ["classroom.google.com"],   # always kept by the free mail pre-filter (P33)
    "gmail_measure": None,                # {"threads", "avg_tokens", "est_total_tokens"} from measure_gmail
    "health_summary": None,
    "build_sample": None,                 # item ids chosen by `misty build --limit N`, or None for everything
}


class Settings:
    """Key-value settings stored as JSON in the `settings` table, with code defaults."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(key)
        row = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else DEFAULTS[key]

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.con.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

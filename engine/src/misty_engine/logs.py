"""Content-free logging. Only ids, codes, numbers and class names can ever reach the log file."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ID = re.compile(r"^[a-z]{2}_[0-9a-f]{8,32}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,47}(?::[a-z][a-z_]{0,23})?$")   # CAP_PURPOSE:judge is a code with a closed-set tag
_IDENT = re.compile(r"^[a-z0-9][a-z0-9_.\-/:]{0,63}$")
_CLASS = re.compile(r"^[A-Z][A-Za-z0-9]{0,47}$")

ALLOWED_FIELDS = frozenset({
    "item_id", "job_id", "build_id", "suggestion_id", "kind", "source", "status", "model",
    "method", "phase", "attempt", "ms", "usd", "count", "n", "code", "exc", "bytes",
    "tokens_in", "tokens_out", "online", "http", "health",
    "keep", "conf", "bad", "cached", "schema", "ok", "pages", "lines", "unlocked",
    "finish", "reasoning", "purpose", "why",
    "mentions", "resolved", "created", "plain", "rejected", "merged", "pending", "pairs", "needed", "qualifying",
})


class LogPolicyError(ValueError):
    pass


_log = logging.getLogger("misty")


def log_event(code: str, /, level: int = logging.INFO, **fields) -> None:
    if not _CODE.match(code):
        raise LogPolicyError("event code")
    for k, v in fields.items():
        if k not in ALLOWED_FIELDS:
            raise LogPolicyError(f"field:{k}")
        if v is None or isinstance(v, (bool, int, float)):
            continue
        if isinstance(v, str) and any(p.match(v) for p in (_ID, _CODE, _IDENT, _CLASS)):
            continue
        raise LogPolicyError(f"value:{k}")
    _log.log(level, code, extra={"misty_fields": fields})


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fields = getattr(record, "misty_fields", None)
        if record.name == "misty" and fields is not None:
            body = " ".join([str(record.msg)] + [f"{k}={v}" for k, v in fields.items()])
        else:
            body = f"THIRD_PARTY logger={record.name}"
            if record.exc_info and record.exc_info[0]:
                body += f" exc={record.exc_info[0].__name__}"
        return f"{ts} {record.levelname} {body}"


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    h = RotatingFileHandler(logs_dir / "engine.log", maxBytes=10 * 1024 * 1024, backupCount=5)
    h.setFormatter(SafeFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.WARNING)
    _log.setLevel(logging.INFO)
    logging.captureWarnings(True)

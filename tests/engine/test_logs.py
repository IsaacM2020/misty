import logging

import pytest

from misty_engine.logs import LogPolicyError, log_event, setup_logging

pytestmark = pytest.mark.usefixtures("restore_logging")


def test_rejects_free_text_value():
    with pytest.raises(LogPolicyError):
        log_event("COMPILE_DONE", item_id="Dear Isaac, your password is hunter2")


def test_rejects_unknown_field():
    with pytest.raises(LogPolicyError):
        log_event("COMPILE_DONE", text="anything")


def test_accepts_ids_codes_numbers():
    log_event("OR_CALL", job_id="jb_0123456789abcdef", model="anthropic/claude-haiku-5.5",
              ms=812, usd=0.0012, code="HTTP_503", exc="ConnectError", online=True)


def test_accepts_cap_purpose_code_but_not_free_text_after_the_colon():
    log_event("JOB_FAIL", job_id="jb_0123456789abcdef", code="CAP_PURPOSE:judge")
    for bad in ("CAP_PURPOSE:Dear Isaac", "CAP_PURPOSE:hunter2 is my password", "CAP_PURPOSE:"):
        with pytest.raises(LogPolicyError):
            log_event("JOB_FAIL", code=bad)


def test_file_output_and_third_party_redaction(misty_home):
    setup_logging(misty_home.logs)
    log_event("BOOT", count=3)
    logging.getLogger("httpx").warning("POST https://x/?q=secret-thing body=hello world")
    try:
        raise ValueError("my secret content")
    except ValueError:
        logging.getLogger("somelib").exception("failed on %s", "private text")
    for h in logging.getLogger().handlers:
        h.flush()
    out = (misty_home.logs / "engine.log").read_text()
    assert "BOOT count=3" in out
    assert "secret" not in out and "hello world" not in out and "private text" not in out
    assert "THIRD_PARTY logger=somelib exc=ValueError" in out


def test_rotation_settings(misty_home):
    setup_logging(misty_home.logs)
    h = logging.getLogger().handlers[0]
    assert h.maxBytes == 10 * 1024 * 1024 and h.backupCount == 5

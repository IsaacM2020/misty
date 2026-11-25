import pytest

from misty_engine.scrub import find_secrets, is_secret_file, luhn_ok, scrub_text

POSITIVE = [
    ("api_key", "key: sk-or-v1-0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d"),
    ("api_key", "OPENAI sk-proj-AbCdEf0123456789AbCdEf0123"),
    ("api_key", "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    ("api_key", "ghp_0123456789abcdefABCDEF0123456789abcd"),
    ("api_key", "xoxb-123456789012-abcdefABCDEF"),
    ("api_key", "AKIAIOSFODNN7EXAMPLE"),
    ("api_key", "GROQ=gsk_abcdefghijklmnopqrstuvwxyz0123"),
    ("api_key", "hf" + "_abcdefghijklmnopqrstuvwxyzABCDEFGH"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpM"),
    ("password", "password: Hunter2Canary!"),
    ("password", "PWD=abc123xyz"),
    ("password", 'api_key = "xyz-secret-value"'),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"),
    ("card", "card 4111 1111 1111 1111 exp"),
    ("nric", "NRIC S1234567D here"),
    ("nric", "fin g7654321x"),
    ("bank_account", "DBS account no: 123-456789-0"),
    ("bank_account", "transfer to 123-45678-9 pls"),
    ("high_entropy", "token Zx9Qm2Lp8Rt4Vw6Yb1Nc3Kd5Hf7Jg0Sa"),
]


@pytest.mark.parametrize("kind,text", POSITIVE)
def test_redacts(kind, text):
    r = scrub_text(text)
    assert f"[REDACTED:{kind}]" in r.text and r.counts[kind] >= 1


NEGATIVE = [
    "Meeting at 3pm about the iGEM wiki, room 4-12.",
    "/Users/me/Notes/docs/superpowers/specs/2026-10-14-misty-context-layer-design.md",
    "uuid 123e4567-e89b-12d3-a456-426614174000",
    "Call me at 9123 4567",
    "card 4111 1111 1111 1112 is not luhn-valid",
    "Score was 187/4 in 20 overs",
    "error code 404 on the page",
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_leaves_normal_text(text):
    assert scrub_text(text).text == text


def test_otp_only_in_messages():
    msg = "Your verification code is 482913. Don't share it."
    assert "[REDACTED:otp]" in scrub_text(msg, "message").text
    assert "482913" in scrub_text(msg, "file").text


def test_scrub_is_idempotent_and_detector_agrees():
    once = scrub_text("password: x1 and sk-or-v1-" + "a1" * 20).text
    assert scrub_text(once).text == once
    assert find_secrets(once) == []


def test_luhn():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")


@pytest.mark.parametrize("name,secret", [
    (".env", True), (".env.local", True), ("server.pem", True), ("id_rsa", True), ("id_ed25519.pub", True),
    ("cert.p12", True), ("vault.kdbx", True), ("my-passwords.csv", True), ("API_TOKEN.txt", True),
    ("credentials.json", True), ("notes.md", False), ("physics-ia.pdf", False),
])
def test_secret_files(name, secret):
    assert is_secret_file(f"/Users/me/Desktop/{name}") is secret


def test_timestamp_in_file_name_is_not_a_card_number():
    # 20260605121306 happens to pass the Luhn check; a file name must not be blocked or redacted for it
    assert scrub_text("council-transcript-20260605-121306.md").counts == {}
    assert scrub_text("card 4539 1488 0343 6467").counts == {"card": 1}

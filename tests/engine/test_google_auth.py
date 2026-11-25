import json

import pytest

from misty_engine import keychain
from misty_engine.errors import Fatal
from misty_engine.sources import google_auth
from misty_engine.sources.google_auth import SCOPES, begin_auth, import_client_json


class FakeCreds:
    def to_json(self):
        return json.dumps({"token": "t", "refresh_token": "r"})


def test_scopes_are_read_plus_the_two_writes_isaac_chose():
    assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",
                      "https://www.googleapis.com/auth/calendar.readonly",
                      "https://www.googleapis.com/auth/calendar.events",
                      "https://www.googleapis.com/auth/gmail.compose")


def test_an_old_read_only_sign_in_keeps_reading_and_grants_no_writes():
    keychain.set_secret("google_token", json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                                                     "scopes": list(google_auth.READ_SCOPES)}))
    assert google_auth.CAL_ADD not in google_auth.granted()
    from google.oauth2.credentials import Credentials
    seen = {}
    real = Credentials.from_authorized_user_info

    def spy(info, scopes=None):
        seen["scopes"] = scopes
        return real(info, scopes)
    import unittest.mock as um
    with um.patch.object(Credentials, "from_authorized_user_info", side_effect=spy):
        try:
            google_auth.credentials()
        except Exception:
            pass
    assert seen["scopes"] == list(google_auth.READ_SCOPES)          # a refresh never asks for scopes he didn't grant


def test_wrong_account_refused_and_nothing_stored():
    with pytest.raises(Fatal) as e:
        begin_auth(False, flow_runner=lambda ob: FakeCreds(), email_fn=lambda c: "other@example.com")
    assert e.value.code == "WRONG_ACCOUNT" and keychain.get_secret("google_token") is None


def test_right_account_stores_token():
    email = begin_auth(False, flow_runner=lambda ob: FakeCreds(),
                       email_fn=lambda c: "you@example.com")
    assert email == "you@example.com" and keychain.get_secret("google_token")


def test_import_client_json_validates():
    import_client_json(json.dumps({"installed": {"client_id": "a", "client_secret": "b"}}))
    assert google_auth.has_client()
    for bad in ("not json", json.dumps({"web": {}}), json.dumps({"installed": {"client_id": "", "client_secret": ""}})):
        with pytest.raises(Fatal) as e:
            import_client_json(bad)
        assert e.value.code == "BAD_CLIENT_JSON"


def test_no_token_means_needs_auth():
    with pytest.raises(Fatal) as e:
        google_auth.credentials()
    assert e.value.code == "NEEDS_AUTH"


def test_no_send_or_full_scope_anywhere_in_source():
    import pathlib
    src = pathlib.Path(google_auth.__file__).parent
    for f in src.glob("*.py"):
        text = f.read_text()
        for bad in ("gmail.send", "gmail.modify", "auth/calendar\"", "mail.google.com"):
            assert bad not in text, f"{bad} in {f.name}"

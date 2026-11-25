"""Google OAuth (installed-app flow, read-only scopes) with tokens in the Keychain only."""
from __future__ import annotations

import json
import os
from typing import Literal

from .. import keychain
from ..errors import Fatal

READ_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",
               "https://www.googleapis.com/auth/calendar.readonly")
# D0 (Isaac, 16 Nov 2026): she may add calendar events and make Gmail drafts. Never send mail, never the full calendar scope.
CAL_ADD = "https://www.googleapis.com/auth/calendar.events"
MAIL_DRAFT = "https://www.googleapis.com/auth/gmail.compose"
SCOPES = READ_SCOPES + (CAL_ADD, MAIL_DRAFT)
ALLOWED_ACCOUNT = os.environ.get("MISTY_GOOGLE_ACCOUNT", "you@example.com").lower()   # the one Google account Misty may read; set it to yours


def import_client_json(text: str) -> None:
    try:
        d = json.loads(text)
        inst = d["installed"]
        if not inst["client_id"] or not inst["client_secret"]:
            raise KeyError
    except (ValueError, KeyError, TypeError):
        raise Fatal("BAD_CLIENT_JSON")
    keychain.set_secret("google_client", json.dumps(d))


def has_client() -> bool:
    return keychain.get_secret("google_client") is not None


def _run_flow(open_browser: bool):
    from google_auth_oauthlib.flow import InstalledAppFlow
    raw = keychain.get_secret("google_client")
    if not raw:
        raise Fatal("NO_GOOGLE_CLIENT")
    flow = InstalledAppFlow.from_client_config(json.loads(raw), list(SCOPES))
    # gives up before the app's 300 s wait does, so an abandoned sign-in tab never leaves a server running behind
    return flow.run_local_server(host="127.0.0.1", port=0, open_browser=open_browser, timeout_seconds=280)


def _profile_email(creds) -> str:
    svc = service("gmail", creds)
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def begin_auth(open_browser: bool = True, *, flow_runner=_run_flow, email_fn=_profile_email) -> str:
    creds = flow_runner(open_browser)
    email = email_fn(creds).lower()
    if email != ALLOWED_ACCOUNT:
        raise Fatal("WRONG_ACCOUNT")             # nothing is stored
    keychain.set_secret("google_token", creds.to_json())
    return email


def credentials():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    raw = keychain.get_secret("google_token")
    if not raw:
        raise Fatal("NEEDS_AUTH")
    info = json.loads(raw)
    # the token's own granted scopes: asking a refresh for scopes he never granted fails, and reading must keep working
    # until he signs in again for the D4/D5 writes
    creds = Credentials.from_authorized_user_info(info, info.get("scopes") or list(READ_SCOPES))
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError:
            raise Fatal("NEEDS_AUTH")
        keychain.set_secret("google_token", creds.to_json())
    return creds


def granted() -> set[str]:
    """The scopes his stored sign-in actually allows (empty when he never signed in)."""
    raw = keychain.get_secret("google_token")
    if not raw:
        return set()
    sc = json.loads(raw).get("scopes") or list(READ_SCOPES)
    return set(sc.split() if isinstance(sc, str) else sc)


def service(name: Literal["gmail", "calendar"], creds=None):
    from googleapiclient.discovery import build
    version = {"gmail": "v1", "calendar": "v3"}[name]
    return build(name, version, credentials=creds or credentials(), cache_discovery=False)


def http_factory(svc):
    """A function giving each worker thread its own authorised HTTP object (httplib2 must not be shared between threads),
    or None when `svc` is not a real Google client."""
    creds = getattr(getattr(svc, "_http", None), "credentials", None)
    if creds is None:
        return None
    import google_auth_httplib2
    import httplib2
    return lambda: google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=20))

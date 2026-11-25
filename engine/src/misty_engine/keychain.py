"""macOS Keychain access. Only the engine touches secrets (service ai.misty)."""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "ai.misty"
NAMES = ("openrouter", "google_client", "google_token")


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete_all() -> int:
    n = 0
    for name in NAMES:
        try:
            keyring.delete_password(SERVICE, name)
            n += 1
        except PasswordDeleteError:
            pass
    return n

"""Names for phone numbers and emails, from the local Contacts database (read-only)."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DEFAULT_DIR = Path.home() / "Library" / "Application Support" / "AddressBook"


class ContactsResolver:
    def __init__(self, ab_dir: Path = DEFAULT_DIR):
        self._phone: dict[str, str] = {}
        self._email: dict[str, str] = {}
        self.dbs_found = 0
        self.dbs_failed = 0
        ab_dir = Path(ab_dir)
        # iCloud/Google accounts live under Sources/<id>/, "On My Mac" contacts in the top-level file
        dbs = sorted(ab_dir.glob("Sources/*/AddressBook-v22.abcddb")) + sorted(ab_dir.glob("AddressBook-v22.abcddb"))
        for db in dbs:
            self.dbs_found += 1
            try:
                self._load(db)
            except sqlite3.Error:
                self.dbs_failed += 1

    def _load(self, db: Path) -> None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            names: dict[int, str] = {}
            for pk, first, last, org in con.execute(
                    "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD"):
                n = " ".join(p for p in (first, last) if p) or (org or "")
                if n:
                    names[pk] = n
            for owner, num in con.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                d = re.sub(r"\D", "", num or "")
                if owner in names and len(d) >= 6:
                    self._phone.setdefault(d[-8:], names[owner])
            for owner, addr in con.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if owner in names and addr:
                    self._email.setdefault(addr.lower(), names[owner])
        finally:
            con.close()

    def name_for(self, handle: str) -> str | None:
        if not handle:
            return None
        if "@" in handle:
            return self._email.get(handle.lower())
        d = re.sub(r"\D", "", handle)
        return self._phone.get(d[-8:]) if len(d) >= 6 else None

"""Identity index: every current and historical identifier -> user_id."""
import csv
import re
from collections import defaultdict

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) >= 8 else None


class Identities:
    def __init__(self, dataset):
        self.users = {}                      # user_id -> current identity row
        self.emails = defaultdict(set)       # user_id -> every email ever held
        self.phones = defaultdict(set)
        self.hashes = defaultdict(set)
        with open(dataset / "user_identities.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uid = row["user_id"]
                self.users[uid] = row
                self._add(uid, "email", row["email"])
                self._add(uid, "phone", row["phone"])
                self._add(uid, "email_hash", row["email_hash"])
        with open(dataset / "user_audit_log.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for value in (row["old_value"], row["new_value"]):
                    self._add(row["user_id"], row["field_name"], value)
        self.by_email = {e: u for u, es in self.emails.items() for e in es}
        self.by_phone = {p: u for u, ps in self.phones.items() for p in ps}
        self.by_hash = {h: u for u, hs in self.hashes.items() for h in hs}

    def _add(self, uid, field, value):
        if not value:
            return
        if field == "email":
            self.emails[uid].add(value.strip().lower())
        elif field == "phone":
            key = phone_key(value)
            if key:
                self.phones[uid].add(key)
        elif field == "email_hash":
            self.hashes[uid].add(value.strip().lower())

    def resolve_email(self, email):
        """Return (user_id, 'current' | 'historical' | None)."""
        email = email.strip().lower()
        uid = self.by_email.get(email)
        if uid is None:
            return None, None
        return uid, "current" if self.users[uid]["email"].lower() == email else "historical"

    def match_value(self, value):
        """Users a single field value identifies, with the identifier kind."""
        if not isinstance(value, str) or not value:
            return set()
        v = value.strip().lower()
        hits = set()
        if v in self.by_email:
            hits.add((self.by_email[v], "email"))
        if v in self.by_hash:
            hits.add((self.by_hash[v], "email_hash"))
        if USER_RE.fullmatch(v) and v in self.users:
            hits.add((v, "user_id"))
        key = phone_key(v) if re.fullmatch(r"[+\d ()./-]{8,}", v) else None
        if key and key in self.by_phone:
            hits.add((self.by_phone[key], "phone"))
        return hits

    def is_current(self, uid, how, value):
        """Is `value` (matched as kind `how`) one of the user's current identifiers?"""
        row = self.users[uid]
        v = value.strip().lower()
        if how == "email":
            return v == row["email"].lower()
        if how == "email_hash":
            return v == row["email_hash"].lower()
        if how == "phone":
            return phone_key(v) == phone_key(row["phone"])
        return True  # user_id never changes

    def match_text(self, text):
        """Users identified anywhere inside free text (messages, PDF segments)."""
        hits = set()
        for m in EMAIL_RE.findall(text or ""):
            hits |= self.match_value(m)
        for m in USER_RE.findall(text or ""):
            hits |= self.match_value(m)
        for m in HASH_RE.findall(text or ""):
            hits |= self.match_value(m)
        return hits

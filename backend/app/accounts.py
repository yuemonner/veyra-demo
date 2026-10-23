"""Accounts for a private Veyra instance: workspaces, people, sessions and share links.

One workspace = one organisation. Each workspace has its OWN case database (a separate SQLite file), so one organisation's
cases, imports and machine context can never appear in another's. People sign in with an email and a password
(scrypt hash; the password itself is never stored). Sessions are random tokens in an HttpOnly cookie; only their
SHA-256 is stored, so the accounts file alone does not let anyone sign in. Accounts are created by an operator
with the command line below; there is no self sign-up.

    python -m app.accounts add-workspace "ETH RSL"
    python -m app.accounts add-user --workspace eth-rsl --email m@example.org --name "Matthias"   # prints a one-time password
    python -m app.accounts reset-password --email m@example.org
    python -m app.accounts disable-user --email m@example.org
    python -m app.accounts list

Data lives in VEYRA_DATA_DIR (default .local-private/): accounts.db and workspaces/<id>/engine.db.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SESSION_DAYS = 14
SHARE_DAYS_DEFAULT = 14
LOCKOUT_FAILURES, LOCKOUT_WINDOW_S = 5, 15 * 60
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  password_hash TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_failures (email TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shares (token_hash TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL, case_id TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL);
"""


class AccountError(ValueError):
    pass


def data_dir() -> Path:
    return Path(os.getenv("VEYRA_DATA_DIR") or Path(__file__).resolve().parents[2] / ".local-private")


def workspace_db(workspace_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", workspace_id or ""):
        raise AccountError("Invalid workspace id")
    folder = data_dir() / "workspaces" / workspace_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "engine.db"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), digest)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if not slug:
        raise AccountError("Give the workspace a name with letters or digits")
    return slug


class Accounts:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else data_dir() / "accounts.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ----- workspaces and people -----
    def add_workspace(self, name: str) -> Dict[str, Any]:
        wid = _slug(name)
        if self.db.execute("SELECT 1 FROM workspaces WHERE id = ?", (wid,)).fetchone():
            raise AccountError(f"Workspace '{wid}' already exists")
        self.db.execute("INSERT INTO workspaces VALUES (?,?,?)", (wid, name.strip(), time.time()))
        workspace_db(wid)
        return {"id": wid, "name": name.strip()}

    def add_user(self, *, workspace_id: str, email: str, name: str, password: Optional[str] = None) -> Dict[str, Any]:
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise AccountError("Give a valid email address")
        if not self.db.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone():
            raise AccountError(f"No workspace '{workspace_id}'")
        if not name.strip():
            raise AccountError("Give the person's name: it is shown on every record they make")
        password = password or secrets.token_urlsafe(12)
        if len(password) < 10:
            raise AccountError("Passwords need at least 10 characters")
        uid = "usr-" + secrets.token_hex(6)
        try:
            self.db.execute("INSERT INTO users VALUES (?,?,?,?,?,0,?)", (uid, email, name.strip()[:120], workspace_id, hash_password(password), time.time()))
        except sqlite3.IntegrityError:
            raise AccountError(f"{email} already has an account") from None
        return {"id": uid, "email": email, "name": name.strip(), "workspace_id": workspace_id, "password": password}

    def set_password(self, email: str, password: Optional[str] = None) -> str:
        password = password or secrets.token_urlsafe(12)
        if len(password) < 10:
            raise AccountError("Passwords need at least 10 characters")
        cur = self.db.execute("UPDATE users SET password_hash = ? WHERE email = ?", (hash_password(password), email.strip().lower()))
        if not cur.rowcount:
            raise AccountError(f"No account for {email}")
        self.db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (email.strip().lower(),))
        return password

    def set_disabled(self, email: str, disabled: bool = True) -> None:
        cur = self.db.execute("UPDATE users SET disabled = ? WHERE email = ?", (int(disabled), email.strip().lower()))
        if not cur.rowcount:
            raise AccountError(f"No account for {email}")
        if disabled:
            self.db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (email.strip().lower(),))

    def listing(self) -> List[Dict[str, Any]]:
        out = []
        for w in self.db.execute("SELECT * FROM workspaces ORDER BY id"):
            people = [{"email": u["email"], "name": u["name"], "disabled": bool(u["disabled"])}
                      for u in self.db.execute("SELECT * FROM users WHERE workspace_id = ? ORDER BY email", (w["id"],))]
            out.append({"workspace": w["id"], "name": w["name"], "people": people})
        return out

    # ----- signing in -----
    def login(self, email: str, password: str) -> Dict[str, Any]:
        email = (email or "").strip().lower()
        now = time.time()
        self.db.execute("DELETE FROM login_failures WHERE at < ?", (now - LOCKOUT_WINDOW_S,))
        failures = self.db.execute("SELECT COUNT(*) FROM login_failures WHERE email = ?", (email,)).fetchone()[0]
        if failures >= LOCKOUT_FAILURES:
            raise AccountError("Too many failed sign-ins. Try again in 15 minutes.")
        user = self.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        ok = bool(user) and not user["disabled"] and check_password(password or "", user["password_hash"])
        if not user:
            check_password(password or "", hash_password("timing-equaliser"))  # same work whether or not the account exists
        if not ok:
            self.db.execute("INSERT INTO login_failures VALUES (?,?)", (email, now))
            raise AccountError("Email or password is not right")
        self.db.execute("DELETE FROM login_failures WHERE email = ?", (email,))
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_hash_token(token), user["id"], now, now + SESSION_DAYS * 86400))
        return {"token": token, "user": self._public(user)}

    def session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        row = self.db.execute("SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_hash_token(token),)).fetchone()
        if not row or row["disabled"] or row["expires_at"] < time.time():
            return None
        return self._public(row)

    def user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
        return self._public(row) if row and not row["disabled"] else None

    def logout(self, token: Optional[str]) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    def _public(self, user) -> Dict[str, Any]:
        w = self.db.execute("SELECT name FROM workspaces WHERE id = ?", (user["workspace_id"],)).fetchone()
        return {"id": user["id"], "email": user["email"], "name": user["name"], "workspace_id": user["workspace_id"], "workspace_name": w["name"] if w else user["workspace_id"]}

    # ----- share links (read-only report of one case, no sign-in) -----
    def create_share(self, *, workspace_id: str, case_id: str, created_by: str, days: int = SHARE_DAYS_DEFAULT) -> Dict[str, Any]:
        if not 1 <= days <= 90:
            raise AccountError("A share link lasts between 1 and 90 days")
        token = secrets.token_urlsafe(24)
        sid = "shr-" + secrets.token_hex(5)
        now = time.time()
        self.db.execute("INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)", (_hash_token(token), sid, workspace_id, case_id, created_by, now, now + days * 86400))
        return {"id": sid, "token": token, "case_id": case_id, "expires_at": now + days * 86400}

    def shares(self, workspace_id: str, case_id: str) -> List[Dict[str, Any]]:
        return [{"id": r["id"], "created_by": r["created_by"], "created_at": r["created_at"], "expires_at": r["expires_at"], "revoked": r["revoked_at"] is not None,
                 "active": r["revoked_at"] is None and r["expires_at"] > time.time()}
                for r in self.db.execute("SELECT * FROM shares WHERE workspace_id = ? AND case_id = ? ORDER BY created_at DESC", (workspace_id, case_id))]

    def revoke_share(self, workspace_id: str, share_id: str) -> None:
        cur = self.db.execute("UPDATE shares SET revoked_at = ? WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL", (time.time(), share_id, workspace_id))
        if not cur.rowcount:
            raise AccountError("No such active share link")

    def resolve_share(self, token: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute("SELECT * FROM shares WHERE token_hash = ?", (_hash_token(token or ""),)).fetchone()
        if not row or row["revoked_at"] is not None or row["expires_at"] < time.time():
            return None
        return {"workspace_id": row["workspace_id"], "case_id": row["case_id"], "expires_at": row["expires_at"], "created_by": row["created_by"]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.accounts", description="Manage workspaces and people of a private Veyra instance")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add-workspace").add_argument("name")
    p = sub.add_parser("add-user")
    p.add_argument("--workspace", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password-file", help="read the password from this file instead of generating one")
    p = sub.add_parser("reset-password")
    p.add_argument("--email", required=True)
    for cmd in ("disable-user", "enable-user"):
        sub.add_parser(cmd).add_argument("--email", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)
    accounts = Accounts()
    try:
        if args.cmd == "add-workspace":
            print(json.dumps(accounts.add_workspace(args.name)))
        elif args.cmd == "add-user":
            password = Path(args.password_file).read_text().strip() if args.password_file else None
            user = accounts.add_user(workspace_id=args.workspace, email=args.email, name=args.name, password=password)
            print(f"Account created for {user['email']} in workspace {user['workspace_id']}.")
            if not password:
                print(f"One-time password (give it to the person privately; it is not stored): {user['password']}")
        elif args.cmd == "reset-password":
            print(f"New password for {args.email} (not stored): {accounts.set_password(args.email)}")
        elif args.cmd in ("disable-user", "enable-user"):
            accounts.set_disabled(args.email, args.cmd == "disable-user")
            print("Done.")
        else:
            print(json.dumps(accounts.listing(), indent=1))
    except AccountError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    finally:
        accounts.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Accounts of a private instance (no web server needed). Test passwords are generated here."""
import secrets
import time

import pytest

from app.accounts import AccountError, Accounts, check_password, hash_password, workspace_db


@pytest.fixture()
def acc(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_DATA_DIR", str(tmp_path))
    a = Accounts()
    a.add_workspace("Acme Robotics")
    yield a
    a.close()


def test_password_is_hashed_never_stored():
    pw = secrets.token_urlsafe(12)
    stored = hash_password(pw)
    assert pw not in stored and check_password(pw, stored) and not check_password(pw + "x", stored)
    assert hash_password(pw) != stored  # salted


def test_sign_in_session_and_sign_out(acc):
    pw = secrets.token_urlsafe(12)
    acc.add_user(workspace_id="acme-robotics", email="Ana@Acme.test", name="Ana", password=pw)
    out = acc.login("ana@acme.test", pw)
    assert acc.session(out["token"])["workspace_id"] == "acme-robotics"
    assert out["token"] not in acc.db.execute("SELECT group_concat(token_hash) FROM sessions").fetchone()[0]
    acc.logout(out["token"])
    assert acc.session(out["token"]) is None


def test_disabled_and_reset_end_sessions(acc):
    pw = secrets.token_urlsafe(12)
    acc.add_user(workspace_id="acme-robotics", email="ana@acme.test", name="Ana", password=pw)
    token = acc.login("ana@acme.test", pw)["token"]
    acc.set_password("ana@acme.test")
    assert acc.session(token) is None
    with pytest.raises(AccountError):
        acc.login("ana@acme.test", pw)


def test_rules(acc):
    with pytest.raises(AccountError):
        acc.add_user(workspace_id="nope", email="a@b.test", name="A")
    with pytest.raises(AccountError):
        acc.add_user(workspace_id="acme-robotics", email="not-an-email", name="A")
    with pytest.raises(AccountError):
        acc.add_user(workspace_id="acme-robotics", email="a@b.test", name="A", password="short")
    with pytest.raises(AccountError):
        workspace_db("../escape")
    assert acc.add_user(workspace_id="acme-robotics", email="a@b.test", name="A")["password"]  # generated


def test_share_expiry(acc, monkeypatch):
    s = acc.create_share(workspace_id="acme-robotics", case_id="case-1", created_by="Ana", days=1)
    assert acc.resolve_share(s["token"])["case_id"] == "case-1"
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 2 * 86400)
    assert acc.resolve_share(s["token"]) is None

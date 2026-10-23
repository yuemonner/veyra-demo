"""Private instance mode (VEYRA_PRIVATE=true): sign-in, one database per workspace, share links.

What changes when it is on:
- Only /auth, /engine, /share and /health answer. The older demo APIs are switched off.
- Every /engine request needs a signed-in person (HttpOnly session cookie). Their workspace decides which database
  is used, so a person only ever sees their own workspace's cases.
- Every record is attributed to the signed-in person: the name sent by the browser is replaced by theirs. That
  makes "a different person must review" real.
- Writes also need the header x-veyra-request (a browser cannot send it cross-site without CORS permission).
- The demo story, the synthetic live event, webhooks, connectors and the AI agent are off. The agent would send
  case data to an external model: it is allowed only with VEYRA_PRIVATE_ALLOW_AGENT=true, set by the operator once
  the data owner has agreed.
- /share/<token> shows one case's read-only report, without sign-in, until the link expires or is revoked.
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.accounts import AccountError, Accounts, workspace_db

COOKIE = "veyra_session"
_accounts: Optional[Accounts] = None


def private() -> bool:
    return os.getenv("VEYRA_PRIVATE") == "true"


def accounts() -> Accounts:
    global _accounts
    if _accounts is None:
        _accounts = Accounts()
    return _accounts


ALLOWED_PREFIXES = ("/auth/", "/engine/", "/share/", "/health")
BLOCKED = (re.compile(r"^/engine/demo/"), re.compile(r"^/engine/webhooks/"), re.compile(r"^/engine/cases/[^/]+/acquire$"))
AGENT = re.compile(r"^/engine/cases/[^/]+/agent$")


async def gate(request: Request, call_next):
    path = request.url.path
    if not path.startswith(ALLOWED_PREFIXES) or any(p.match(path) for p in BLOCKED):
        return JSONResponse({"detail": "Not available on this instance"}, status_code=404)
    if request.method == "OPTIONS":
        return await call_next(request)
    if path.startswith("/engine/"):
        user = current_user(request)
        if not user:
            return JSONResponse({"detail": "Sign in to continue.", "code": "login_required"}, status_code=401)
        if AGENT.match(path) and os.getenv("VEYRA_PRIVATE_ALLOW_AGENT") != "true":
            return JSONResponse({"detail": "The AI agent is off on this instance: it would send case data to an external model. "
                                           "Your operator can turn it on once your organisation agrees."}, status_code=403)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-veyra-request") != "1":
            return JSONResponse({"detail": "Missing x-veyra-request header"}, status_code=403)
        request.state.user = user
    return await call_next(request)


def local_demo_user(request: Request):
    """Local demos only: VEYRA_LOCAL_SIGNIN_AS=<email> treats every request from this computer as that person.
    Refused for any request that does not come from 127.0.0.1, so it cannot open a deployed instance."""
    email = os.getenv("VEYRA_LOCAL_SIGNIN_AS")
    host = request.client.host if request.client else ""
    if not email or host not in ("127.0.0.1", "::1"):
        return None
    return accounts().user_by_email(email)


def current_user(request: Request):
    return accounts().session(request.cookies.get(COOKIE)) or local_demo_user(request)


def store_path_for(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return workspace_db(user["workspace_id"])


def actor_for(request: Request, given: str) -> str:
    """In private mode the signed-in person is the actor, whatever the browser sent."""
    user = getattr(request.state, "user", None) if private() else None
    return user["name"] if user else given


# ---------- sign-in ----------
auth = APIRouter(prefix="/auth", tags=["Sign-in (private instance)"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


def _cookie(response: Response, token: str, max_age: int):
    response.set_cookie(COOKIE, token, max_age=max_age, httponly=True, samesite="lax", path="/",
                        secure=os.getenv("VEYRA_COOKIE_SECURE") == "true")


@auth.post("/login")
def login(body: LoginIn, response: Response):
    if not private():
        raise HTTPException(404, "Sign-in is only used on a private instance")
    try:
        out = accounts().login(body.email, body.password)
    except AccountError as error:
        raise HTTPException(401, str(error)) from None
    _cookie(response, out["token"], 14 * 86400)
    return {"user": out["user"]}


@auth.post("/logout")
def logout(request: Request, response: Response):
    accounts().logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@auth.get("/me")
def me(request: Request):
    if not private():
        return {"private": False, "user": None}
    user = current_user(request)
    if not user:
        return JSONResponse({"private": True, "user": None, "code": "login_required"}, status_code=401)
    return {"private": True, "user": user, "signin_off": local_demo_user(request) is not None, "agent_allowed": os.getenv("VEYRA_PRIVATE_ALLOW_AGENT") == "true"}


# ---------- share links ----------
shares = APIRouter(tags=["Share links (private instance)"])


class ShareIn(BaseModel):
    days: int = Field(default=14, ge=1, le=90)


@shares.post("/engine/cases/{case_id}/shares")
def create_share(case_id: str, body: ShareIn, request: Request):
    if not private():
        raise HTTPException(404, "Share links are used on a private instance")
    from app.store.sqlite_store import CaseStore, StoreError
    store = CaseStore(store_path_for(request))
    try:
        store.case(case_id)
    except StoreError:
        raise HTTPException(404, "Case not found") from None
    finally:
        store.close()
    user = request.state.user
    out = accounts().create_share(workspace_id=user["workspace_id"], case_id=case_id, created_by=user["name"], days=body.days)
    return {"id": out["id"], "path": f"/share/{out['token']}", "expires_at": _iso(out["expires_at"]),
            "note": "Anyone with this link can read this case's report until it expires or you revoke it. The link is shown once."}


@shares.get("/engine/cases/{case_id}/shares")
def list_shares(case_id: str, request: Request):
    if not private():
        return {"shares": []}
    user = request.state.user
    return {"shares": [{**s, "created_at": _iso(s["created_at"]), "expires_at": _iso(s["expires_at"])} for s in accounts().shares(user["workspace_id"], case_id)]}


@shares.delete("/engine/shares/{share_id}")
def revoke_share(share_id: str, request: Request):
    if not private():
        raise HTTPException(404, "Share links are used on a private instance")
    try:
        accounts().revoke_share(request.state.user["workspace_id"], share_id)
    except AccountError as error:
        raise HTTPException(404, str(error)) from None
    return {"revoked": share_id}


@shares.get("/share/{token}", response_class=HTMLResponse)
def shared_report(token: str):
    if not private():
        raise HTTPException(404, "Not found")
    found = accounts().resolve_share(token)
    if not found:
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Link not available</title><body style='font-family:system-ui;padding:40px'>"
                            "<h1>This link is not available</h1><p>It has expired or was revoked. Ask the person who shared it for a new one.</p></body>", status_code=404)
    from app.workflows import report as report_html
    from app.store.sqlite_store import CaseStore
    store = CaseStore(workspace_db(found["workspace_id"]))
    try:
        body = report_html.render(store, found["case_id"], authenticated=True)
    finally:
        store.close()
    banner = (f"<div style='font:14px system-ui;background:#eef3f8;border-bottom:1px solid #c9d6e3;padding:10px 16px'>Shared read-only by "
              f"{html.escape(found['created_by'])}. This link works until {html.escape(_iso(found['expires_at'])[:16].replace('T', ' '))} UTC.</div>")
    body = body.replace("<body>", "<body>" + banner, 1) if "<body>" in body else banner + body
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")

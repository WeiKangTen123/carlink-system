"""Sign-in check for every request to the API.

A middleware rather than a FastAPI dependency so it also covers the /files
mount (case photos and PDFs), which route dependencies never see. Before
this, the API trusted whatever reached it and the only lock was nginx's
shared basic-auth password.

Also refuses state-changing requests sent from another site's page. The
session cookie is SameSite=Lax, but sslip.io hosts may count as one "site"
to the browser, so any *.sslip.io page could otherwise post to this API with
the user's cookie attached.
"""
import re
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.core import SESSION_COOKIE, resolve_session
from app.reports.db import SessionLocal

PUBLIC_PATHS = {
    "/health",
    "/auth/login",
    "/auth/logout",
    # Signing up and using a one-time link (app/api/signup.py). /auth/signup
    # answers GET (are signups open?) and POST (sign up).
    "/auth/signup",
    "/auth/links/lookup",
    "/auth/links/accept",
}
# What a user who must change their password can still do.
PASSWORD_CHANGE_ALLOWED = {("GET", "/auth/me"), ("POST", "/auth/password")}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Same as the CORS rule in app/api/main.py: the dashboard's dev server.
LOCAL_DEV_ORIGIN = re.compile(r"https?://(localhost|127\.0\.0\.1)(:\d+)?")


def origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        # Browsers send Origin on every cross-site POST/PUT/PATCH/DELETE; a
        # request without one is the dashboard's own server or a script.
        return True
    if LOCAL_DEV_ORIGIN.fullmatch(origin):
        return True
    # Host names only: nginx passes Host without the port ($host), while the
    # browser's Origin carries it when it isn't 80/443 (e.g. :8080).
    host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
    return bool(host) and (urlsplit(origin).hostname or "") == host


def _lookup(token: str | None):
    db = SessionLocal()
    try:
        return resolve_session(db, token)
    finally:
        db.close()


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        # Before the public-path exit too: another site must not be able to
        # sign a visitor in as someone else either.
        if scope["method"] not in _SAFE_METHODS and not origin_allowed(request):
            await JSONResponse({"detail": "Request refused: it came from another site."}, status_code=403)(scope, receive, send)
            return
        if scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        user = await run_in_threadpool(_lookup, request.cookies.get(SESSION_COOKIE))
        if user is None:
            await JSONResponse({"detail": "Not signed in.", "code": "signed_out"}, status_code=401)(scope, receive, send)
            return
        if user.must_change_password and (scope["method"], scope["path"]) not in PASSWORD_CHANGE_ALLOWED:
            await JSONResponse(
                {"detail": "Change your temporary password first.", "code": "password_change_required"},
                status_code=403,
            )(scope, receive, send)
            return

        # request.state reads scope["state"].
        scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)

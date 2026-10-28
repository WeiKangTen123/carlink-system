"""FastAPI dependencies for the user AuthMiddleware already signed in, and
the gates on every route about one case (rules in app/auth/permissions.py)."""
from fastapi import Depends, HTTPException, Request

from app.auth.core import CurrentUser
from app.auth.permissions import can_edit_case, can_view_case
from app.reports.db import SessionLocal
from app.reports.models import Report
from app.storage.files import case_id_for_file

# The one answer for a case that doesn't exist and one the user may not
# see, so a stranger can't tell them apart.
CASE_NOT_FOUND = "Case not found."
NOT_ALLOWED = "You can see this case, but you can't change it."


class ApiError(HTTPException):
    """An error the dashboard tells apart by its code, sent as
    {"detail": ..., "code": ...} (handler in api/main.py), like the
    middleware's password_change_required."""

    def __init__(self, status_code: int, detail: str, code: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def not_allowed(detail: str = NOT_ALLOWED) -> ApiError:
    return ApiError(403, detail, "not_allowed")


def current_user(request: Request) -> CurrentUser:
    user = getattr(request.state, "user", None)
    if user is None:
        # Only reachable on a path the middleware leaves public.
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can do this.")
    return user


def _case_access(report_id: str):
    """Just what the rules look at (not the case data, which can be large:
    /files asks this for every photo on a page)."""
    db = SessionLocal()
    try:
        return db.query(Report.id, Report.owner_id, Report.status).filter(Report.id == report_id).first()
    finally:
        db.close()


def _visible(report_id: str, user: CurrentUser):
    report = _case_access(report_id)
    if report is None or not can_view_case(user, report):
        raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
    return report


def visible_case(report_id: str, user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """For a route that reads one case: 404 unless the user may see it.
    Runs before the request body is checked, so a stranger never learns
    more from a 422 either."""
    _visible(report_id, user)
    return user


def editable_case(report_id: str, user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """For a route that changes one case: 404 as visible_case, then 403
    (code not_allowed) unless the user may change it."""
    if not can_edit_case(user, _visible(report_id, user)):
        raise not_allowed()
    return user


def can_view_file(user: CurrentUser, rel_path: str) -> bool:
    """Whether a /files path (relative to storage_dir) is one of the cases
    this user may see. Asked fresh each time, so a reassigned case's photos
    stop loading at once."""
    case_id = case_id_for_file(rel_path)
    if case_id is None:
        return False
    report = _case_access(case_id)
    return report is not None and can_view_case(user, report)

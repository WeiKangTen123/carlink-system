"""Who may see and do what with a case. Pure rules: no FastAPI, no
database, so the API (app/auth/deps.py), the bot and the tests all ask the
same questions.

`user` is a CurrentUser or a User row; `report` is anything with
.owner_id and .status (a Report row, or a row of just those columns).

    admin                 everything
    "see all cases" tick  every case, Unassigned ones too; works on them,
                          except other people's unfinished drafts
    anyone else           only the cases they own

A case someone can't see is "not found" to them (404, the same answer as a
made-up id), so a stranger can't even confirm it exists. 403 is only for
"you can see it, but this needs someone else".
"""

SCOPES = ("mine", "all", "unassigned")


def is_admin(user) -> bool:
    return getattr(user, "role", None) == "admin"


def can_view_all_cases(user) -> bool:
    return is_admin(user) or bool(getattr(user, "sees_all_cases", False))


def is_staff(user) -> bool:
    """Signs off under the company's name: an admin, or ticked "May sign
    off". Cases owned by anyone else are someone outside the firm's."""
    return is_admin(user) or bool(getattr(user, "may_sign_off", False))


def may_use_company_key(user) -> bool:
    return is_admin(user) or bool(getattr(user, "ai_company_key_allowed", False))


def is_draft(report) -> bool:
    """An unfinished intake (the New Intake wizard)."""
    return report.status == "intake"


def is_signed_off(report) -> bool:
    if report.status == "Signed Off":
        return True
    data = getattr(report, "data", None) or {}
    return (data.get("sign_off") or {}).get("status") == "Signed Off"


def owns(user, report) -> bool:
    return report.owner_id is not None and report.owner_id == user.id


def can_view_case(user, report) -> bool:
    return can_view_all_cases(user) or owns(user, report)


def can_edit_case(user, report) -> bool:
    """A draft is its owner's until it is filed: "see all" staff may look,
    not change it."""
    if not can_view_case(user, report):
        return False
    return is_admin(user) or owns(user, report) or not is_draft(report)


def can_sign_off(user, report) -> bool:
    return is_staff(user) and can_edit_case(user, report) and not is_draft(report)


def can_reopen(user, report) -> bool:
    """Reopening a signed-off case withdraws someone's signature: only the
    signer or an admin. Sign-offs from before sign-in record no account, so
    those need an admin."""
    if not (is_signed_off(report) and can_view_case(user, report)):
        return False
    data = getattr(report, "data", None) or {}
    return is_admin(user) or (data.get("sign_off") or {}).get("signed_by_user_id") == user.id


def can_delete_case(user, report) -> bool:
    """People throw away their own unfinished drafts; deleting a filed case
    is for admins."""
    return is_admin(user) or (is_draft(report) and owns(user, report))


def can_assign(user) -> bool:
    return is_admin(user)


def case_permissions(user, report) -> dict:
    """What the case screen's buttons follow (GET /reports/{id}, GET
    /intake/{id}). The API checks each again when it's used."""
    return {
        "edit": can_edit_case(user, report),
        "sign_off": can_sign_off(user, report),
        "reopen": can_reopen(user, report),
        "delete": can_delete_case(user, report),
        "assign": can_assign(user),
    }


def list_scope(user, requested: str | None) -> str | None:
    """Which cases a list (GET /reports, analytics, taxonomy) covers, or
    None if this user may not ask for that one. Plain users only ever get
    their own; "see all" staff start on their own and may switch; admins
    start on all."""
    if not can_view_all_cases(user):
        return "mine" if requested in (None, "mine") else None
    if requested is None:
        return "all" if is_admin(user) else "mine"
    return requested if requested in SCOPES else None

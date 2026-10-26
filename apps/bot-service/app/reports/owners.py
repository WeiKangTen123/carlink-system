"""Case owners: who a case belongs to, moving it, and the one-time start-up
steps that gave the cases and accounts from before owners theirs.

Every case has one owner (Report.owner_id) or none ("Unassigned"); see
app/auth/permissions.py for what that decides. Run in both the api and the
telegram-bot process.

    python -m app.reports.owners --dry-run

shows what the first start-up of this version does to the database at
DATABASE_URL -- which cases get which owner, which accounts get the staff
ticks -- and changes nothing. Works on a database from before owners too
(e.g. a copy of the live one), so it can be checked before deploying.
"""
import argparse

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.auth.core import audit
from app.auth.permissions import case_permissions, is_admin
from app.reports.models import AuditEntry, Report, User, iso_utc

# The three ticks an admin sets on an account (models.User).
TICKS = ("sees_all_cases", "ai_company_key_allowed", "may_sign_off")
SIGNED_OFF_STATUSES = ("Signed Off", "signed_off")
# History entries that say someone worked on a case, for suggesting an owner.
_EDIT_ACTIONS = ("edited", "annex_a_edited")


def owner_dict(user) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.name, "username": user.username or "", "active": bool(user.active)}


def owners_by_id(db: Session, ids) -> dict:
    """The accounts behind a list of owner ids, in one query: rows with
    id, name, username and active (what owner_dict shows)."""
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = db.query(User.id, User.name, User.username, User.active).filter(User.id.in_(wanted)).all()
    return {u.id: u for u in rows}


def assigned_detail(owner) -> str:
    if owner is None:
        return "Unassigned"
    return f"Assigned to {owner.name} (@{owner.username})" if owner.username else f"Assigned to {owner.name}"


def set_owner(db: Session, report: Report, new_owner, actor) -> bool:
    """Gives the case to new_owner (None = Unassigned) and notes it in the
    case history. False if it was theirs already. The caller checks who may
    (permissions.can_assign) and commits."""
    new_id = new_owner.id if new_owner else None
    if report.owner_id == new_id:
        return False
    report.owner_id = new_id
    audit(db, actor, "assigned", report.id, detail=assigned_detail(new_owner))
    return True


def case_counts(db: Session, user_ids) -> dict[str, dict]:
    """{user id: {open, signed_off, drafts}} for the Users screen, from one
    GROUP BY rather than a count per account."""
    counts = {uid: {"open": 0, "signed_off": 0, "drafts": 0} for uid in user_ids}
    if not counts:
        return counts
    rows = (
        db.query(Report.owner_id, Report.status, func.count())
        .filter(Report.owner_id.in_(list(counts)))
        .group_by(Report.owner_id, Report.status)
        .all()
    )
    for owner_id, status, n in rows:
        bucket = "drafts" if status == "intake" else "signed_off" if status in SIGNED_OFF_STATUSES else "open"
        counts[owner_id][bucket] += n
    return counts


# --------------------------------------------------- who a case points to


def _history_accounts(db: Session, report_id: str | None = None) -> tuple[dict, dict]:
    """From the case history: ({(case, "created"|"finalized"): first account},
    {case: {account: edits}}). Every case's in one query, or one case's."""
    query = db.query(AuditEntry.report_id, AuditEntry.action, AuditEntry.user_id).filter(
        AuditEntry.report_id.isnot(None),
        AuditEntry.user_id.isnot(None),
        AuditEntry.action.in_(("created", "finalized") + _EDIT_ACTIONS),
    )
    if report_id is not None:
        query = query.filter(AuditEntry.report_id == report_id)
    first: dict[tuple[str, str], str] = {}
    edits: dict[str, dict[str, int]] = {}
    for rid, action, uid in query.order_by(AuditEntry.id).all():
        if action in _EDIT_ACTIONS:
            per_case = edits.setdefault(rid, {})
            per_case[uid] = per_case.get(uid, 0) + 1
        else:
            first.setdefault((rid, action), uid)
    return first, edits


def _candidates(report_id: str, data: dict | None, first: dict, edits: dict) -> list[tuple[str, str]]:
    """(account id, why), best first: who created it, who finished its
    intake, who signed it off, who edited it most."""
    found = []
    for action, why in (("created", "created it"), ("finalized", "finished its intake")):
        if (report_id, action) in first:
            found.append((first[(report_id, action)], why))
    signer = ((data or {}).get("sign_off") or {}).get("signed_by_user_id")
    if signer:
        found.append((signer, "signed it off"))
    per_case = edits.get(report_id) or {}
    if per_case:
        found.append((max(per_case, key=per_case.get), "edited it most"))
    return found


def suggested_owner(db: Session, report: Report) -> dict | None:
    """For an admin assigning an Unassigned case: the first active account
    its history points to, or None."""
    first, edits = _history_accounts(db, report.id)
    candidates = _candidates(report.id, report.data, first, edits)
    accounts = owners_by_id(db, [uid for uid, _ in candidates])
    for uid, why in candidates:
        user = accounts.get(uid)
        if user is not None and user.active:
            return {"id": user.id, "name": user.name, "username": user.username or "", "reason": why}
    return None


def access_fields(db: Session, report: Report, user) -> dict:
    """What a case's detail response (GET /reports/{id}, GET /intake/{id})
    says about its owner and what this user may do with it -- and, to an
    admin looking at an Unassigned case, who it should probably go to."""
    owner = owners_by_id(db, [report.owner_id]).get(report.owner_id)
    fields = {"owner": owner_dict(owner), "permissions": case_permissions(user, report)}
    if is_admin(user) and report.owner_id is None:
        fields["suggested_owner"] = suggested_owner(db, report)
    return fields


# ------------------------------------------- one-time start-up steps (db.py)


def _columns(db: Session, table: str) -> set[str]:
    """So the dry run also reads a database from before these columns."""
    return {row[1] for row in db.execute(text(f"PRAGMA table_info({table})"))}


def plan_backfill(db: Session) -> list[dict]:
    """What backfill_case_owners does, one entry per case without an owner.
    The owner is the account on the case's "created" history entry, else
    on its "finalized" one (an intake finished by someone) -- kept even if
    that account is now disabled. Anything else (Telegram, cases from
    before sign-in) stays Unassigned for an admin to hand out."""
    query = db.query(Report.id, Report.channel, Report.status, Report.created_at, Report.data)
    if "owner_id" in _columns(db, "reports"):
        query = query.filter(Report.owner_id.is_(None))
    reports = query.order_by(Report.created_at).all()
    first, edits = _history_accounts(db)
    accounts = owners_by_id(db, set(first.values()) | {u for e in edits.values() for u in e})
    plan = []
    for r in reports:
        owner, reason = None, "no creator recorded"
        for action, why in (("created", "created it"), ("finalized", "finished its intake")):
            owner = accounts.get(first.get((r.id, action)))
            if owner is not None:
                reason = why if owner.active else f"{why} (account now disabled)"
                break
        if owner is None:
            hint = next(
                ((accounts[uid], why) for uid, why in _candidates(r.id, r.data, first, edits) if uid in accounts),
                None,
            )
            if hint:
                reason += f"; suggest {hint[0].name} (@{hint[0].username}), who {hint[1]}"
        plate = (((r.data or {}).get("vehicle_info") or {}).get("plate_number")) or ""
        plan.append({
            "report_id": r.id, "plate": plate, "channel": r.channel, "status": r.status,
            "created_at": r.created_at, "owner": owner, "reason": reason,
        })
    return plan


def backfill_case_owners(db: Session) -> int:
    """Gives cases from before owners theirs (see plan_backfill). Only
    fills empty owners; the caller commits. Returns how many changed."""
    by_owner: dict[str, list[str]] = {}
    for entry in plan_backfill(db):
        if entry["owner"] is not None:
            by_owner.setdefault(entry["owner"].id, []).append(entry["report_id"])
    changed = 0
    for owner_id, report_ids in by_owner.items():
        changed += (
            db.query(Report)
            .filter(Report.id.in_(report_ids), Report.owner_id.is_(None))
            .update({Report.owner_id: owner_id}, synchronize_session=False)
        )
    return changed


def plan_staff_ticks(db: Session) -> list[dict]:
    """What grandfather_staff_ticks does, one entry per non-admin account:
    the people already working here keep what they had before the ticks
    existed (all three on); accounts that signed themselves up (a
    "signed_up" history entry, app/api/signup.py) start with them off.
    Admins have all of it anyway and are left alone."""
    present = [t for t in TICKS if t in _columns(db, "users")]
    signed_up = {uid for (uid,) in db.query(AuditEntry.user_id).filter(AuditEntry.action == "signed_up", AuditEntry.user_id.isnot(None))}
    invited = {uid for (uid,) in db.query(AuditEntry.user_id).filter(AuditEntry.action == "invite_accepted", AuditEntry.user_id.isnot(None))}
    rows = (
        db.query(User.id, User.email, User.username, User.role, User.active, *(getattr(User, t) for t in present))
        .filter(User.role != "admin")
        .order_by(User.created_at, User.id)
        .all()
    )
    plan = []
    for row in rows:
        value = row.id not in signed_up
        reason = "signed themselves up" if not value else "accepted an invite" if row.id in invited else "made by an admin or the server command"
        unset = [t for t in TICKS if t not in present or getattr(row, t) is None]
        plan.append({"user": row, "value": value, "ticks": unset, "reason": reason})
    return plan


def grandfather_staff_ticks(db: Session) -> int:
    """Sets the three ticks once, on the accounts that existed before them
    (see plan_staff_ticks). Only ticks still None change; the caller
    commits. Returns how many accounts changed."""
    targets: dict[tuple[str, bool], list[str]] = {}
    changed = set()
    for entry in plan_staff_ticks(db):
        for tick in entry["ticks"]:
            targets.setdefault((tick, entry["value"]), []).append(entry["user"].id)
            changed.add(entry["user"].id)
    for (tick, value), ids in targets.items():
        column = getattr(User, tick)
        db.query(User).filter(User.id.in_(ids), column.is_(None)).update({column: value}, synchronize_session=False)
    return len(changed)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.reports.owners", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", required=True, help="show the changes; change nothing")
    parser.parse_args()

    from app.reports.db import SessionLocal

    db = SessionLocal()
    try:
        cases = plan_backfill(db)
        print(f"Cases without an owner: {len(cases)}")
        for e in cases:
            owner = f"{e['owner'].name} (@{e['owner'].username})" if e["owner"] else "Unassigned"
            created = iso_utc(e["created_at"])[:10] if e["created_at"] else "?"
            print(f"  {e['report_id']}  {e['plate'] or '-':10} {e['channel']:9} {e['status']:11} {created}  -> {owner}  [{e['reason']}]")
        people = plan_staff_ticks(db)
        print(f"\nAccounts (not admins) whose ticks are still unset: {sum(1 for p in people if p['ticks'])}")
        for p in people:
            if p["ticks"]:
                u = p["user"]
                state = "" if u.active else " (disabled)"
                print(f"  {u.email:40} @{(u.username or ''):24} -> {'all ticks ON ' if p['value'] else 'ticks off   '}  [{p['reason']}]{state}")
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()

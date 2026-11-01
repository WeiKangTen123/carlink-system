"""The Public Inbox (P6): reports members of the public send through the
Telegram bot, before and while staff work on them. A thin HTTP layer: every
rule and every change of state is app/intake/public.py's.

    GET  /inbox?view=new|taken       admins and "see all" staff (else 403)
    GET  /inbox/count                the same people: the sidebar badge
    GET  /inbox/{id}                 anyone who may see the case
    POST /inbox/{id}/take            "see all" staff and admins, untaken only
    POST /inbox/{id}/assign          admins, untaken only, to a vetted account
    POST /inbox/{id}/release         a taken draft's owner, or an admin
    POST /inbox/{id}/discard         untaken: who may take it; taken: its owner or an admin

The case routes run visible_case first (404 to anyone who may not see the
case), then 404 inbox_gone for a report that isn't from the public. A
refused change answers 409 with a code the dashboard explains (public.
refusal): inbox_taken, inbox_filed, inbox_changed, inbox_finalizing,
ai_running, draft_limit, owner_inactive, owner_unvetted.

The list shows the phone masked; the report page (anyone who may see the
case) shows it in full. Never the Telegram user id.
"""
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, update

from app.auth.core import CurrentUser, utc_now
from app.auth.deps import current_user, not_allowed, visible_case
from app.auth.permissions import (
    PUBLIC_CHANNEL,
    can_assign,
    can_discard,
    can_release,
    can_take,
    can_view_all_cases,
    in_inbox,
    is_public,
)
from app.intake import pipeline, public
from app.intake.photos import ordered_photos, photo_label, remove_intake_dir, serialize_photo
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, PublicSubmission, Report, User, iso_utc
from app.reports.owners import owner_dict, owners_by_id
from app.storage.files import disk_space_low

router = APIRouter(prefix="/inbox", tags=["inbox"])

# Untaken reports this close to their 30 days count as expiring soon (GET
# /inbox/count); the list's countdown turns amber then.
EXPIRING_SOON = timedelta(days=3)
STATEMENT_PREVIEW = 140

NOT_STAFF = "Only admins and staff who see all cases can open the public inbox."
NOT_TAKER = "Only admins and staff who see all cases can take public reports."
NOT_ASSIGNER = "Only an admin can give a public report to someone."
NOT_RELEASER = "Only the person who took this report, or an admin, can put it back in the inbox."
NOT_DISCARDER = "Only the person who took this report, or an admin, can discard it."


def _staff_only(user: CurrentUser) -> None:
    if not can_view_all_cases(user):
        raise not_allowed(NOT_STAFF)


def _public_report(db, report_id: str):
    """The columns the rules read, fresh; 404 inbox_gone for a report that
    isn't from the public (visible_case has already run)."""
    row = db.query(Report.id, Report.channel, Report.status, Report.owner_id).filter(Report.id == report_id).first()
    if row is None or row.channel != PUBLIC_CHANNEL:
        raise public.InboxError(404, public.GONE, "inbox_gone")
    return row


def _owner(db, user_id: str | None) -> dict | None:
    return owner_dict(owners_by_id(db, [user_id]).get(user_id)) if user_id else None


def _when(value) -> str | None:
    return iso_utc(value) if value else None


def _preview(text: str | None) -> str:
    """Their words on one line, cut for the list."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= STATEMENT_PREVIEW else flat[: STATEMENT_PREVIEW - 1].rstrip() + "…"


def clear_stale_run(db, report_id: str) -> None:
    """A stage a restart left at sorting/reading, with no run behind it, is
    set to "failed" (as GET /intake/{id} already shows it), so a discard or
    a delete isn't refused as "AI running" for ever. Commits. Written first,
    then the run asked about (pipeline.reserve comes before a run's stage is
    committed), as public.release does."""
    result = db.execute(
        update(IntakeSession)
        .where(IntakeSession.report_id == report_id, IntakeSession.stage.in_(public.RUNNING_STAGES))
        .values(stage="failed", error=public.STALE_RUN, error_code=None, error_by=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount and pipeline.is_running(report_id):
        db.rollback()
        return
    db.commit()


# ------------------------------------------------------------------ reading


@router.get("")
def list_inbox(view: Literal["new", "taken"] = "new", user: CurrentUser = Depends(current_user)) -> dict:
    """new: nobody has taken them; taken: someone has, not filed yet.
    Oldest first. The phone is masked here."""
    _staff_only(user)
    db = SessionLocal()
    try:
        drafts = and_(Report.channel == PUBLIC_CHANNEL, Report.status == "intake")
        which = Report.owner_id.is_(None) if view == "new" else Report.owner_id.isnot(None)
        rows = (
            db.query(PublicSubmission, Report.owner_id)
            .join(Report, Report.id == PublicSubmission.report_id)
            .filter(drafts, which)
            .order_by(PublicSubmission.submitted_at, PublicSubmission.report_id)
            .all()
        )
        ids = [s.report_id for s, _ in rows]
        photos = dict(
            db.query(IntakePhoto.report_id, func.count(IntakePhoto.id))
            .filter(IntakePhoto.report_id.in_(ids))
            .group_by(IntakePhoto.report_id)
            .all()
        ) if ids else {}
        owners = owners_by_id(db, {owner_id for _, owner_id in rows if owner_id})
        new, taken = (
            db.query(
                func.count(case((Report.owner_id.is_(None), 1))),
                func.count(case((Report.owner_id.isnot(None), 1))),
            )
            .select_from(PublicSubmission)
            .join(Report, Report.id == PublicSubmission.report_id)
            .filter(drafts)
            .one()
        )
        items = [
            {
                "id": s.report_id,
                "reference": s.reference,
                "submitted_at": _when(s.submitted_at),
                "expires_at": _when(s.expires_at),
                "sender": {
                    "name": s.submitter_name,
                    "role": s.submitter_role,
                    "role_label": public.ROLES.get(s.submitter_role, public.ROLES["other"]),
                    "phone_masked": public.mask_phone(s.submitter_phone),
                    "phone_kind": s.phone_kind,
                    "telegram_username": s.telegram_username,
                },
                "plate": s.plate,
                "photo_count": photos.get(s.report_id, 0),
                "statement_preview": _preview(s.statement),
                "withdraw_requested": s.withdraw_requested_at is not None,
                "owner": owner_dict(owners.get(owner_id)),
                "taken_at": _when(s.taken_at),
            }
            for s, owner_id in rows
        ]
        return {
            "items": items,
            "counts": {"new": new, "taken": taken},
            # accepting: false while the bot turns the public away for the
            # inbox being full (or the disk; it has more limits of its own).
            "limits": {
                "max_open": public.MAX_OPEN,
                "accepting": new < public.MAX_OPEN and not disk_space_low(public.DISK_HEADROOM_GB),
            },
        }
    finally:
        db.close()


@router.get("/count")
def count_inbox(user: CurrentUser = Depends(current_user)) -> dict:
    """{new, oldest_submitted_at, expiring_soon}: the sidebar badge, the
    Cases chip and the Command Center card."""
    _staff_only(user)
    db = SessionLocal()
    try:
        new, oldest, soon = (
            db.query(
                func.count(PublicSubmission.report_id),
                func.min(PublicSubmission.submitted_at),
                func.count(case((PublicSubmission.expires_at <= utc_now() + EXPIRING_SOON, 1))),
            )
            .join(Report, Report.id == PublicSubmission.report_id)
            .filter(public.untaken_clause())
            .one()
        )
        return {"new": new, "oldest_submitted_at": _when(oldest), "expiring_soon": soon}
    finally:
        db.close()


@router.get("/{report_id}")
def get_inbox_item(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """One public report, read-only, in full: public.submission_info plus
    its photos, the sender's other reports, its owner and what this user
    may do. A filed one says state "filed" (the dashboard opens the case;
    its photos are the case's by then, not listed here)."""
    db = SessionLocal()
    try:
        _public_report(db, report_id)
        report = db.get(Report, report_id)
        info = public.submission_info(db, report)
        if info is None:
            raise public.InboxError(404, public.GONE, "inbox_gone")
        photos = ordered_photos(db, report_id)
        return {
            **info,
            "id": report.id,
            "photos": [serialize_photo(p, photo_label(i)) for i, p in enumerate(photos)],
            # Other reports' references are only for people who may open them.
            "same_sender": public.same_sender(db, report_id) if can_view_all_cases(user) else [],
            "owner": _owner(db, report.owner_id),
            "permissions": {
                "take": can_take(user, report),
                "assign": can_assign(user) and in_inbox(report),
                "release": can_release(user, report),
                "discard": can_discard(user, report),
            },
        }
    finally:
        db.close()


# ------------------------------------------------------------------ changing


@router.post("/{report_id}/take")
def take_report(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """Takes an untaken report as the caller's own draft (then it opens in
    the wizard). 409 inbox_taken ("Taken by S. Lee just now"), inbox_filed,
    draft_limit; 404 inbox_gone."""
    db = SessionLocal()
    try:
        _public_report(db, report_id)
        if not can_view_all_cases(user):
            raise not_allowed(NOT_TAKER)
        # Conditional (public.take): the one that lands first wins.
        public.take(db, report_id, user)
        db.commit()
        return {"id": report_id, "owner": _owner(db, user.id)}
    finally:
        db.close()


class AssignRequest(BaseModel):
    owner_id: str = Field(min_length=1, max_length=64)


@router.post("/{report_id}/assign")
def assign_report(report_id: str, body: AssignRequest, user: CurrentUser = Depends(visible_case)) -> dict:
    """An admin gives an untaken report to an active, vetted account as its
    draft. 409 owner_inactive, owner_unvetted, draft_limit, inbox_taken.
    Once taken, release it first to give it to someone else."""
    db = SessionLocal()
    try:
        _public_report(db, report_id)
        if not can_assign(user):
            raise not_allowed(NOT_ASSIGNER)
        target = db.get(User, body.owner_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        public.assign(db, report_id, target, user)
        db.commit()
        return {"id": report_id, "owner": _owner(db, target.id)}
    finally:
        db.close()


@router.post("/{report_id}/release")
def release_report(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """Puts a taken public draft back in the inbox, as it is now (the AI's
    work and edits stay). Its 30 days run on from when it was sent: one
    past them goes at the next sweep. 409 ai_running, inbox_finalizing,
    inbox_changed (someone released it already), inbox_filed."""
    db = SessionLocal()
    try:
        row = _public_report(db, report_id)
        if not is_public(row) or row.owner_id is None:
            raise public.refusal(db, report_id)
        if not can_release(user, row):
            raise not_allowed(NOT_RELEASER)
        # Only while it is still the owner just read.
        public.release(db, report_id, user, seen_owner=row.owner_id, is_running=pipeline.is_running)
        db.commit()
        return {"id": report_id, "owner": None}
    finally:
        db.close()


class DiscardRequest(BaseModel):
    reason: Literal["spam", "duplicate", "test", "not_vehicle", "other"]
    # "TG-261029-Q8T2", with reason "duplicate".
    duplicate_of: Optional[str] = Field(None, max_length=40)
    # A few words for the history; staff are asked to keep personal details
    # out of it.
    note: Optional[str] = Field(None, max_length=public.MAX_DISCARD_NOTE)
    # Spam only; on unless sent false. Blocks the sender's Telegram for
    # public.BLOCK_FOR.
    block_sender: Optional[bool] = None


@router.post("/{report_id}/discard")
def discard_report(report_id: str, body: DiscardRequest, user: CurrentUser = Depends(visible_case)) -> dict:
    """Deletes a public report not yet filed, with its photos and the
    sender's details, for good. The history keeps the reference and the
    reason, never a name or phone. 422 without a reason; 409 inbox_filed,
    inbox_changed (it changed hands meanwhile), ai_running,
    inbox_finalizing."""
    db = SessionLocal()
    try:
        row = _public_report(db, report_id)
        if not is_public(row):
            raise public.refusal(db, report_id)
        if not can_discard(user, row):
            raise not_allowed(NOT_STAFF if in_inbox(row) else NOT_DISCARDER)
        detail = public.discard_detail(body.reason, body.duplicate_of, body.note)
        clear_stale_run(db, report_id)
        # Only while it is still as just read: in the inbox, or the same
        # owner's; refused while the AI or finalize holds it.
        if not public.delete_submission(
            db,
            report_id,
            seen_owner=row.owner_id,
            actor=user,
            action="discarded",
            detail=detail,
            block=body.reason == "spam" and body.block_sender is not False,
        ):
            raise public.refusal(db, report_id)
        db.commit()
    finally:
        db.close()
    remove_intake_dir(report_id)
    return {"deleted": report_id}

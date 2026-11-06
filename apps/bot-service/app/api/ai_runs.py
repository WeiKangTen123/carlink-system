"""Settings > AI usage: the AI run log (models.AiRun, app/ai/runs.py)
summed over the last days, for admins.

    GET /ai-usage?days=30     7, 30 or 90 (default 30)

What the AI did across every case: how many runs, how many failed and why
(the codes), how often Google said 429 and how long the calls waited,
tokens when the SDK reported them, per key (own or company, last 4 only),
per kind of work, per person, and the newest failures with the case to
open. Admin only, like System info: it shows every case and every account.

Nothing here comes from what a member of the public sent: plate_or_ref is
the case's own plate or Our Ref -- and for a report from the public only
Our Ref, as its plate in the case data is the one the sender typed.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.ai.runs import FAILED_STATUSES, actor_names, effective_error_code, effective_status
from app.auth.core import CurrentUser
from app.auth.deps import require_admin
from app.auth.permissions import PUBLIC_CHANNEL
from app.reports.db import SessionLocal
from app.reports.models import AiRun, Report, iso_utc

router = APIRouter(tags=["ai-usage"])

USAGE_DAYS = (7, 30, 90)
MAX_FAILURES = 50


def _plate_or_ref(report: Report | None) -> str | None:
    if report is None:
        return None  # deleted since
    data = report.data or {}
    ref = (data.get("report_id") or "").strip() or None
    # Filed or not: its plate came from the sender.
    if report.channel == PUBLIC_CHANNEL:
        return ref
    plate = ((data.get("vehicle_info") or {}).get("plate_number") or "").strip()
    return plate or ref


@router.get("/ai-usage")
def ai_usage(days: int = 30, admin: CurrentUser = Depends(require_admin)) -> dict:
    if days not in USAGE_DAYS:
        raise HTTPException(status_code=400, detail="days must be 7, 30 or 90.")
    now = datetime.now(timezone.utc)
    # Naive UTC, as SQLite hands DateTime columns back.
    since = (now - timedelta(days=days)).replace(tzinfo=None)
    db = SessionLocal()
    try:
        rows = db.query(AiRun).filter(AiRun.started_at >= since).order_by(AiRun.started_at.desc(), AiRun.id).all()
        names = actor_names(db, {r.actor_user_id for r in rows})

        failed_by_code: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        by_key: dict[tuple, dict] = {}
        by_user: dict[str | None, dict] = {}
        failures: list[tuple[AiRun, str, str | None]] = []
        waits_429 = 0
        waited = 0.0
        tried = False
        tokens_in: int | None = None
        tokens_out: int | None = None
        failed = 0
        for row in rows:
            status = effective_status(row, now)
            code = effective_error_code(row, status)
            is_failed = status in FAILED_STATUSES
            if is_failed:
                failed += 1
                key = code or "error"
                failed_by_code[key] = failed_by_code.get(key, 0) + 1
                if len(failures) < MAX_FAILURES:
                    failures.append((row, status, code))
            by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
            key_row = by_key.setdefault(
                (row.key_source, row.key_last4),
                {"key_source": row.key_source, "key_last4": row.key_last4, "runs": 0, "failed": 0},
            )
            key_row["runs"] += 1
            key_row["failed"] += int(is_failed)
            user_row = by_user.setdefault(
                row.actor_user_id, {"name": names.get(row.actor_user_id) if row.actor_user_id else None, "runs": 0, "failed": 0}
            )
            user_row["runs"] += 1
            user_row["failed"] += int(is_failed)
            for attempt in row.attempts or []:
                tried = True
                if attempt.get("outcome") == "429":
                    waits_429 += 1
                value = attempt.get("waited_s")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    waited += value
            if row.prompt_tokens is not None:
                tokens_in = (tokens_in or 0) + row.prompt_tokens
            if row.output_tokens is not None:
                tokens_out = (tokens_out or 0) + row.output_tokens

        reports = {}
        wanted = {row.report_id for row, _, _ in failures if row.report_id}
        if wanted:
            reports = {r.id: r for r in db.query(Report).filter(Report.id.in_(wanted)).all()}
        return {
            "days": days,
            "runs": len(rows),
            "failed": failed,
            "failed_by_code": failed_by_code,
            # Model tries Google answered 429, and every second the calls
            # waited (rate-limit slots and the delays 429s asked for); None
            # when no call was made at all.
            "waits_429": waits_429,
            "waited_s": round(waited, 1) if tried else None,
            # Only what the SDK reported; None when it reported nothing.
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "by_key": sorted(by_key.values(), key=lambda k: (-k["runs"], k["key_source"] or "", k["key_last4"] or "")),
            "by_kind": by_kind,
            "by_user": sorted(by_user.values(), key=lambda u: (-u["runs"], u["name"] or "")),
            "failures": [
                {
                    "at": iso_utc(row.started_at),
                    "report_id": row.report_id,
                    "plate_or_ref": _plate_or_ref(reports.get(row.report_id)),
                    "kind": row.kind,
                    "status": status,
                    "error_code": code,
                    "actor_name": names.get(row.actor_user_id) if row.actor_user_id else None,
                }
                for row, status, code in failures
            ],
        }
    finally:
        db.close()

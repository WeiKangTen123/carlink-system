"""Settings > AI accuracy, "What people changed" (step 2B): the part
observations of signed-off cases (app/reports/observations.py) counted, for
admins.

    GET /admin/observations/summary?from=YYYY-MM-DD&to=YYYY-MM-DD

from / to: Singapore dates, both days included; either may be left out.
Answer: {from, to, totals, by_part, by_make, by_area}. Each count group has
cases, kept, removed (on the AI's list, taken off by people), added (put on
by people: the AI missed them), added_prompted (of those, added from a
suggestion), dismissed (a suggestion marked Not damaged) and left (a
suggestion nobody decided); totals also parts and cases_without_ai_draft
(cases with no AI draft to compare with, left out of the counts). A part is
its family, both sides together, or "Other / Not Listed: <name>" (the
scrubbed name); a make as the price book writes it, "Unknown" when the case
has none; an area front / rear / side / roof / underbody / cabin / wheel.

Counts only: no case id, Our Ref, plate or anyone's name. Admin only.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.core import CurrentUser
from app.auth.deps import require_admin
from app.reports.db import SessionLocal
from app.reports.observations import summarize

router = APIRouter(tags=["observations"])

# The firm works in Singapore time: a day is a Singapore day.
SG_TIME = timezone(timedelta(hours=8))


def _day(value: Optional[str], name: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a date: YYYY-MM-DD.") from None


def _utc_start(day: date) -> datetime:
    """The day's first moment in Singapore, as naive UTC (how SQLite hands
    DateTime columns back)."""
    return datetime.combine(day, time(), SG_TIME).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/admin/observations/summary")
def observations_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    first, last = _day(from_, "from"), _day(to, "to")
    if first and last and first > last:
        raise HTTPException(status_code=400, detail="from must not be after to.")
    start = _utc_start(first) if first else None
    end = _utc_start(last + timedelta(days=1)) if last else None
    db = SessionLocal()
    try:
        return {
            "from": first.isoformat() if first else None,
            "to": last.isoformat() if last else None,
            **summarize(db, start, end),
        }
    finally:
        db.close()

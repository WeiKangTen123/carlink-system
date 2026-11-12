"""Settings > AI accuracy (contract 8 L, 2026-11-12): what surveyors kept,
removed, added or changed of the AI's drafts before signing off -- the
AiAccuracy rows app/reports/accuracy.py records at sign-off -- for admins.

    GET /admin/ai-accuracy?since=YYYY-MM-DD   a Singapore date; default every case

Answer: totals and the same per month (Singapore time, with that month's
most missed and removed parts), each with the rates the page shows; the
cases, newest first (id, Our Ref, make and model, when signed off, the
models that read it, its metrics); and the parts the AI missed or people
removed most often, by family (both sides together), with the Annex A
lines people add or remove most often. Only
cases signed off now: a row left by a reopen that couldn't remove it is
not counted. Plus whether signed-off cases teach the parts history
(settings.learn_from_signed_off) and how many are in it.

The rates (null when there is nothing to divide by):
  parts_kept / parts_removed / parts_added  of every part on either list
                                            (kept + removed + added)
  wrong_side, remarks_changed,              of the parts kept
  condition_changed
  lines_kept / lines_removed                of the Annex A lines the machine
                                            writes from the AI's damage list
  lines_added                               of the signed-off lines
  price_changed                             of the kept lines the machine priced
  fields_kept / fields_changed /            of the fields the AI filled
  fields_emptied

Admin only, like AI usage: it lists every case. Never a plate: Our Ref and
the make and model as the case data has them.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import defer

from app.auth.core import CurrentUser
from app.auth.deps import require_admin
from app.config import settings
from app.parts.history import family_and_side, normalise_item
from app.parts.learn import learned_cases
from app.reports.accuracy import ANNEX_COUNTS, DAMAGE_COUNTS, FIELD_COUNTS
from app.reports.db import SessionLocal
from app.reports.models import AiAccuracy, Report, iso_utc
from app.reports.taxonomy import OTHER_PART

router = APIRouter(tags=["ai-accuracy"])

# The firm works in Singapore time: a month is a Singapore month.
SG_TIME = timezone(timedelta(hours=8))
MAX_CASES = 1000
TOP = 10


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _month(at: datetime) -> str:
    aware = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return aware.astimezone(SG_TIME).strftime("%Y-%m")


def _summed(metrics: list[dict]) -> dict:
    """Totals over cases' metrics, with the rates. Annex A only over the
    cases it was compared on (an Annex A rebuilt from the AI's draft)."""
    damage, annex, fields = Counter(), Counter(), Counter()
    compared = 0
    for m in metrics:
        d = m.get("damage") if isinstance(m.get("damage"), dict) else {}
        a = m.get("annex_a") if isinstance(m.get("annex_a"), dict) else {}
        f = m.get("fields") if isinstance(m.get("fields"), dict) else {}
        for key in DAMAGE_COUNTS:
            damage[key] += _int(d.get(key))
        for key in ("final", "final_machine", "final_typed"):
            annex[key] += _int(a.get(key))
        if a.get("machine") is not None:
            compared += 1
            for key in ANNEX_COUNTS:
                annex[key] += _int(a.get(key))
        for key in FIELD_COUNTS:
            fields[key] += _int(f.get(key))
    parts = damage["kept"] + damage["removed"] + damage["added"]
    rates = {
        "parts_kept": _ratio(damage["kept"], parts),
        "parts_removed": _ratio(damage["removed"], parts),
        "parts_added": _ratio(damage["added"], parts),
        "wrong_side": _ratio(damage["side_changed"], damage["kept"]),
        "remarks_changed": _ratio(damage["remarks_changed"], damage["kept"]),
        "condition_changed": _ratio(damage["condition_changed"], damage["kept"]),
        "lines_kept": _ratio(annex["kept"], annex["machine"]),
        "lines_removed": _ratio(annex["removed"], annex["machine"]),
        "lines_added": _ratio(annex["added"], annex["kept"] + annex["added"]),
        "price_changed": _ratio(annex["price_changed"], annex["priced_kept"]),
        "fields_kept": _ratio(fields["kept"], fields["ai_filled"]),
        "fields_changed": _ratio(fields["changed"], fields["ai_filled"]),
        "fields_emptied": _ratio(fields["emptied"], fields["ai_filled"]),
    }
    return {
        "cases": len(metrics),
        "damage": {key: damage[key] for key in DAMAGE_COUNTS},
        "annex_a": {"cases_compared": compared, **{key: annex[key] for key in ANNEX_COUNTS + ("final", "final_machine", "final_typed")}},
        "fields": {key: fields[key] for key in FIELD_COUNTS},
        "rates": rates,
    }


def _family(label: str) -> str:
    """A part as the most-missed list counts it: its family, both sides
    and "(side undetermined)" together; a named unlisted part by its name."""
    if label.startswith(OTHER_PART + ":"):
        return label[len(OTHER_PART) + 1:].strip() or OTHER_PART
    return family_and_side(label)[0]


def _top_parts(metrics: list[dict], key: str) -> list[dict]:
    """The families in damage[key] of most cases (each case counts once)."""
    counts: Counter = Counter()
    for m in metrics:
        names = (m.get("damage") or {}).get(key) if isinstance(m.get("damage"), dict) else None
        counts.update({_family(str(n)) for n in names or [] if str(n).strip()})
    return [{"part": part, "cases": n} for part, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]]


def _top_lines(metrics: list[dict], key: str) -> list[dict]:
    """The Annex A wordings in annex_a[key] of most cases, by wording
    (history.normalise_item), shown as most often written."""
    counts: Counter = Counter()
    wordings: dict[str, Counter] = {}
    for m in metrics:
        names = (m.get("annex_a") or {}).get(key) if isinstance(m.get("annex_a"), dict) else None
        seen = set()
        for name in names or []:
            text = str(name).strip()
            norm = normalise_item(text)
            if not norm:
                continue
            wordings.setdefault(norm, Counter())[text] += 1
            seen.add(norm)
        counts.update(seen)
    out = []
    for norm, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]:
        common = wordings[norm].most_common()
        top = max(c for _, c in common)
        out.append({"line": sorted(w for w, c in common if c == top)[0], "cases": n})
    return out


def _since(value: Optional[str]) -> datetime | None:
    if not value:
        return None
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be a date: YYYY-MM-DD.")
    # Naive UTC, as SQLite hands DateTime columns back.
    return datetime.combine(day, time(), SG_TIME).astimezone(timezone.utc).replace(tzinfo=None)


def _vehicle(data: dict) -> str | None:
    vehicle = data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {}
    name = " ".join(str(v).strip() for v in (vehicle.get("make"), vehicle.get("model")) if v and str(v).strip())
    return name or None


@router.get("/admin/ai-accuracy")
def ai_accuracy(since: Optional[str] = None, admin: CurrentUser = Depends(require_admin)) -> dict:
    start = _since(since)
    db = SessionLocal()
    try:
        query = (
            db.query(AiAccuracy, Report)
            .join(Report, Report.id == AiAccuracy.report_id)
            .options(defer(Report.ai_draft))
            .filter(Report.status == "Signed Off")
        )
        if start is not None:
            query = query.filter(AiAccuracy.signed_off_at >= start)
        rows = query.order_by(AiAccuracy.signed_off_at.desc(), AiAccuracy.report_id).limit(MAX_CASES).all()

        cases, by_month = [], {}
        for row, report in rows:
            metrics = row.metrics if isinstance(row.metrics, dict) else {}
            data = report.data if isinstance(report.data, dict) else {}
            month = _month(row.signed_off_at)
            by_month.setdefault(month, []).append(metrics)
            cases.append({
                "report_id": row.report_id,
                "our_ref": str(data.get("report_id") or "").strip() or None,
                "vehicle": _vehicle(data),
                "signed_off_at": iso_utc(row.signed_off_at),
                "month": month,
                "models": row.models if isinstance(row.models, list) else [],
                "version": row.version,
                "metrics": metrics,
            })
        all_metrics = [c["metrics"] for c in cases]
        return {
            "since": start and since.strip(),
            "totals": _summed(all_metrics),
            "months": [
                {"month": month, **_summed(metrics), "most_missed": _top_parts(metrics, "added_parts"),
                 "most_removed": _top_parts(metrics, "removed_parts")}
                for month, metrics in sorted(by_month.items(), reverse=True)
            ],
            "cases": cases,
            "most_missed": _top_parts(all_metrics, "added_parts"),
            "most_removed": _top_parts(all_metrics, "removed_parts"),
            "lines_added_most": _top_lines(all_metrics, "added_lines"),
            "lines_removed_most": _top_lines(all_metrics, "removed_lines"),
            "learning": {"on": settings.learn_from_signed_off, "cases": len(learned_cases(db))},
        }
    finally:
        db.close()

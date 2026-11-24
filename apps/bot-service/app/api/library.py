"""The parts library, read-only (L-library-cycle.md A, step 2A).

    GET /library        {version, core, staff, areas, assemblies, components,
                         twins, fittings, other}
    GET /library/tree   {version, core, staff, areas: area -> assembly ->
                         component -> fittings}

Any signed-in account may read the library: the core is the code's own
parts (app/parts/library_core.json), nothing about any case. What comes
from cases and past reports goes to staff only (decision G9: an admin, a
"see all cases" or a "may sign off" account):

  - "uses" and "conditions" on each component and twin: how many filed
    cases list it, and the conditions written most -- counts, never a case;
  - "fittings": the price book's wordings as seed fittings under their
    part (library.seed_fittings) -- names only, no price, plate, case,
    make or model (decision D3: staff see fitting names).

Anyone else gets the same shapes without them (no "uses", fittings []),
and "staff": false says why. The staff extras are cached for a few minutes
(CACHE_SECONDS; a new or removed case or past line refreshes them at once).
Nothing here writes: no table, no AI call.
"""
import threading
import time

from fastapi import APIRouter, Depends
from sqlalchemy import func

from app.auth.core import CurrentUser
from app.auth.deps import current_user
from app.auth.permissions import can_view_all_cases, is_staff
from app.config import settings
from app.intake import public
from app.parts import history, library
from app.reports.db import SessionLocal
from app.reports.models import PastAnnexLine, Report

router = APIRouter(tags=["library"])

# The staff extras (counts over every filed case, the price book's seeds)
# read every case's data and every past line: kept for a few minutes, and
# worked out again at once when the cases or the price book change (more
# or fewer filed cases, a newer one, more or fewer past lines). An edit to
# a filed case's parts shows here within CACHE_SECONDS. One copy for all
# staff: the extras say nothing about who asks. (Review L9: the design has
# these counted nightly; once 2B's part_observations is in, count from it.)
CACHE_SECONDS = 300
_cache: dict = {"key": None, "at": 0.0, "value": None}
_cache_lock = threading.Lock()


def sees_library_counts(user) -> bool:
    """Decision G9: usage counts and the price book's fitting names are for
    staff -- admins, "see all cases" and "may sign off" accounts."""
    return can_view_all_cases(user) or is_staff(user)


def _case_datas(db):
    """The filed cases' data (drafts and the public inbox left out, as the
    analytics count them). Only the data column is read."""
    query = db.query(Report.data).filter(Report.status != "intake").filter(public.not_untaken_clause())
    return (row[0] for row in query.yield_per(200))


def _extras_key(db) -> tuple:
    """What the staff extras depend on, cheaply: the filed cases' count and
    newest, the price book's line count and newest id, and the switch that
    decides which learned lines count."""
    cases = (db.query(func.count(Report.id), func.max(Report.created_at))
             .filter(Report.status != "intake").filter(public.not_untaken_clause()).one())
    lines = db.query(func.count(PastAnnexLine.id), func.max(PastAnnexLine.id)).one()
    return tuple(cases), tuple(lines), bool(settings.learn_from_signed_off), library.load_core()["core"]


def staff_extras() -> tuple[dict, list[dict]]:
    """(usage per component or twin key, seed fittings), from the cache
    while it is fresh and its key unchanged. Callers never change them."""
    db = SessionLocal()
    try:
        key = _extras_key(db)
        with _cache_lock:
            if _cache["key"] == key and time.monotonic() - _cache["at"] < CACHE_SECONDS:
                return _cache["value"]
        core = library.load_core()
        value = (library.usage(_case_datas(db), core), library.seed_fittings(history.past_lines(db), core))
    finally:
        db.close()
    with _cache_lock:
        _cache.update(key=key, at=time.monotonic(), value=value)
    return value


def library_for(user) -> dict:
    """GET /library's answer for this user: the core, with the staff-only
    facts added to copies of its entries for staff. The cached core itself
    is never changed."""
    core = library.load_core()
    staff = sees_library_counts(user)
    components, twins, fittings = core["components"], core["twins"], []
    if staff:
        counts, seeds = staff_extras()

        def with_counts(entry: dict) -> dict:
            got = counts.get(entry["key"]) or {"uses": 0, "conditions": []}
            return {**entry, "uses": got["uses"], "conditions": [list(c) for c in got["conditions"]]}

        components = [with_counts(c) for c in components]
        twins = [with_counts(t) for t in twins]
        fittings = [dict(f) for f in seeds]
    return {
        "format": core["format"],
        "version": core["version"],
        "core": core["core"],
        "built_from": core["built_from"],
        "staff": staff,
        "areas": core["areas"],
        "assemblies": core["assemblies"],
        "components": components,
        "twins": twins,
        "fittings": fittings,
        "families": core["families"],
        "other": core["other"],
    }


@router.get("/library")
def get_library(user: CurrentUser = Depends(current_user)) -> dict:
    return library_for(user)


@router.get("/library/tree")
def get_library_tree(user: CurrentUser = Depends(current_user)) -> dict:
    lib = library_for(user)
    return {"format": lib["format"], "version": lib["version"], "core": lib["core"], "staff": lib["staff"],
            "areas": library.tree(lib)}

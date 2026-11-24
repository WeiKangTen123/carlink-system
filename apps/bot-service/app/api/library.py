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
and "staff": false says why. Nothing here writes: no table, no AI call.
"""
from fastapi import APIRouter, Depends

from app.auth.core import CurrentUser
from app.auth.deps import current_user
from app.auth.permissions import can_view_all_cases, is_staff
from app.intake import public
from app.parts import history, library
from app.reports.db import SessionLocal
from app.reports.models import Report

router = APIRouter(tags=["library"])


def sees_library_counts(user) -> bool:
    """Decision G9: usage counts and the price book's fitting names are for
    staff -- admins, "see all cases" and "may sign off" accounts."""
    return can_view_all_cases(user) or is_staff(user)


def _case_datas(db):
    """The filed cases' data (drafts and the public inbox left out, as the
    analytics count them). Only the data column is read."""
    query = db.query(Report.data).filter(Report.status != "intake").filter(public.not_untaken_clause())
    return (row[0] for row in query.yield_per(200))


def library_for(user) -> dict:
    """GET /library's answer for this user: a copy of the core, with the
    staff-only facts added for staff."""
    core = library.copy_core()
    staff = sees_library_counts(user)
    fittings: list[dict] = []
    if staff:
        db = SessionLocal()
        try:
            counts = library.usage(_case_datas(db), core)
            fittings = library.seed_fittings(history.past_lines(db), core)
        finally:
            db.close()
        for entry in core["components"] + core["twins"]:
            got = counts.get(entry["key"]) or {"uses": 0, "conditions": []}
            entry["uses"] = got["uses"]
            entry["conditions"] = got["conditions"]
    return {
        "format": core["format"],
        "version": core["version"],
        "core": core["core"],
        "built_from": core["built_from"],
        "staff": staff,
        "areas": core["areas"],
        "assemblies": core["assemblies"],
        "components": core["components"],
        "twins": core["twins"],
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

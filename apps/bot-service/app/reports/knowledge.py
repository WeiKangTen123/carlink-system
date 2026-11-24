"""The knowledge stamp (L-library D.3, step 2B): which versions of the
knowledge produced a result -- so results can always be told apart by
what produced them, and "before" and "after" a change sit side by side.

    {"library": None, "links": "L1:06bbebfa", "car_map": None, "prompt": "v1",
     "models": ["gemini-3.5-flash-lite"]}

  library  the parts library's version: None until step 3C keeps one.
  links    the link table's version and the first 8 hex of its sha256 as
           git keeps the file (line endings LF, so a Windows checkout and
           the server agree); None while the linked parts are off
           (settings.intake_links "off") or the table didn't load.
  car_map  the car map's version ("K1.0") while settings.intake_car_map is
           "shadow" or "on" and the map loaded; None while it is off.
  prompt   the reading prompt (settings.intake_read_prompt, "v1" / "v2"),
           on a reading run only (READ_KINDS); None on any other run.
  models   the models that answered.

Two kinds of stamp:

  stamp()       the versions loaded NOW: what an AI run is working with.
                ai/runs.py writes it on each AiRun row (AiRun.knowledge)
                when the run starts, and its answering models when it ends.
  case_stamp()  what made a CASE's AI draft: taken from the case's own
                runs' stamps -- never from what is live when it is asked
                (a case read under one link table and signed off after a
                release would otherwise carry the new one). The newest
                successful reading run's stamp (READ_KINDS), else the
                newest successful run of the draft's other steps
                (DRAFT_KINDS); with no stamped run (a case read before the
                stamp existed) library, links, car_map and prompt are None.
                models: the reading models (accuracy.reading_models). It is
                written into the case data at filing (filing.file_case_data)
                and at sign-off (data.knowledge), and onto each
                part_observations row (also by the backfill).

No AI call, no write. stamp() and case_stamp() never raise: a stamp that
can't be made is None, and nothing it is written beside may fail for it.
"""
import hashlib
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# The first hex characters of a file's sha256 kept in the stamp: enough to
# tell two versions of a table apart, short enough to read.
FINGERPRINT = 8
KEYS = ("library", "links", "car_map", "prompt", "models")
MAX_MODELS = 6
# The runs that read the photos and drafted the case (accuracy.READ_KINDS):
# the only ones whose stamp names the reading prompt.
READ_KINDS = ("read", "redraft", "redetect")
# The other steps that made the AI's draft and its suggestions: the sort,
# the zone reads, the second look, the re-check and the linked check
# (ai/runs.py KINDS).
DRAFT_KINDS = READ_KINDS + ("read_zones", "second_look", "check", "link_check", "sort")


@lru_cache(maxsize=8)
def _lf_sha256(path: str) -> str | None:
    """sha256 of a text file as git keeps it (LF), as car_map.sha256_lf;
    cached: the files ship with the code and never change while it runs."""
    try:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def links_version() -> str | None:
    """"L1:06bbebfa", or None (see the module's docstring)."""
    from app.intake import links

    if getattr(settings, "intake_links", "off") == "off" or links.TABLE is None or not links.TABLE_VERSION:
        return None
    sha = _lf_sha256(str(links.TABLE_PATH))
    return f"{links.TABLE_VERSION}:{sha[:FINGERPRINT]}" if sha else str(links.TABLE_VERSION)


def car_map_version() -> str | None:
    if getattr(settings, "intake_car_map", "off") not in ("shadow", "on"):
        return None
    from app.intake import car_map

    version = car_map.MAP.get("version") if isinstance(car_map.MAP, dict) else None
    return str(version)[:20] if version else None


def _models(models) -> list[str]:
    out: list[str] = []
    for model in models or []:
        if isinstance(model, str) and model.strip() and model.strip() not in out:
            out.append(model.strip()[:60])
    return out[:MAX_MODELS]


def empty(models=()) -> dict:
    """A stamp with nothing known but the models."""
    return {"library": None, "links": None, "car_map": None, "prompt": None, "models": _models(models)}


def stamp(models=(), kind: str | None = "read") -> dict | None:
    """The versions loaded now, with these models; the reading prompt only
    for a reading run (kind in READ_KINDS). None (logged) if it can't be
    made."""
    try:
        return {
            "library": None,
            "links": links_version(),
            "car_map": car_map_version(),
            "prompt": str(getattr(settings, "intake_read_prompt", "v1") or "v1") if kind in READ_KINDS else None,
            "models": _models(models),
        }
    except Exception as exc:
        logger.warning("Knowledge stamp: none this time (%s)", type(exc).__name__)
        return None


def answered_models(attempts) -> list[str]:
    """The models that answered an AI run's calls (its attempts with
    outcome ok), first answer first."""
    return _models(a.get("model") for a in attempts or [] if isinstance(a, dict) and a.get("outcome") == "ok")


def case_stamp(db, report_id: str | None) -> dict | None:
    """What made the case's AI draft, from its own runs (see the module's
    docstring); None only when it can't be worked out at all (logged)."""
    if not report_id:
        return None
    try:
        from app.reports.accuracy import reading_models
        from app.reports.models import AiRun

        rows = (
            db.query(AiRun.kind, AiRun.knowledge)
            .filter(AiRun.report_id == report_id, AiRun.status == "ok", AiRun.kind.in_(DRAFT_KINDS))
            .order_by(AiRun.started_at.desc(), AiRun.id.desc())
            .all()
        )
        stamped = [(kind, k) for kind, k in rows if isinstance(k, dict)]
        found = next((k for kind, k in stamped if kind in READ_KINDS), None) or next((k for _, k in stamped), None)
        models = reading_models(db, report_id)
        base = clean(found)
        return {**base, "models": _models(models)} if base else empty(models)
    except Exception as exc:
        logger.warning("Knowledge stamp of a case: none this time (%s)", type(exc).__name__)
        return None


def clean(value) -> dict | None:
    """A stored stamp read back: the known keys only, short strings; None
    when it isn't one."""
    if not isinstance(value, dict):
        return None
    out: dict = {}
    for key in KEYS:
        item = value.get(key)
        if key == "models":
            out[key] = _models(item if isinstance(item, list) else [])
        elif isinstance(item, (str, int)) and not isinstance(item, bool):
            out[key] = item if isinstance(item, int) else item[:40]
        else:
            out[key] = None
    return out

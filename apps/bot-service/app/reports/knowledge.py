"""The knowledge stamp (L-library D.3, step 2B): which versions of the
knowledge the app works with -- so a result can always be told apart by
what produced it, and "before" and "after" a change sit side by side.

    {"library": None, "links": "L1:06bbebfa", "car_map": None, "prompt": "v1",
     "models": ["gemini-3.5-flash-lite"]}

  library  the parts library's version: None until step 3C keeps one.
  links    the link table's version and the first 8 hex of its sha256 as
           git keeps the file (line endings LF, so a Windows checkout and
           the server agree); None while the linked parts are off
           (settings.intake_links "off") or the table didn't load.
  car_map  the car map's version ("K1.0") while settings.intake_car_map is
           "shadow" or "on" and the map loaded; None while it is off.
  prompt   the reading prompt (settings.intake_read_prompt, "v1" / "v2").
  models   the models that answered: one AI run's (ai/runs.py writes it
           on AiRun.knowledge), or the reads of one case at sign-off
           (accuracy.reading_models; data.knowledge and each
           part_observations row).

Pure reading of what is loaded; no AI call, no write. stamp() never
raises: a stamp that can't be made is None, and nothing it is written
beside may fail for it.
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


def stamp(models=()) -> dict | None:
    """The stamp now, with these models; None (logged) if it can't be made."""
    try:
        return {
            "library": None,
            "links": links_version(),
            "car_map": car_map_version(),
            "prompt": str(getattr(settings, "intake_read_prompt", "v1") or "v1"),
            "models": _models(models),
        }
    except Exception as exc:
        logger.warning("Knowledge stamp: none this time (%s)", type(exc).__name__)
        return None


def answered_models(attempts) -> list[str]:
    """The models that answered an AI run's calls (its attempts with
    outcome ok), first answer first."""
    return _models(a.get("model") for a in attempts or [] if isinstance(a, dict) and a.get("outcome") == "ok")


def case_stamp(db, report_id: str) -> dict | None:
    """The stamp of a case at sign-off: today's versions with the models
    whose reading drafted it (accuracy.reading_models)."""
    try:
        from app.reports.accuracy import reading_models

        return stamp(reading_models(db, report_id))
    except Exception as exc:
        logger.warning("Knowledge stamp of a case: none this time (%s)", type(exc).__name__)
        return None


def clean(value) -> dict | None:
    """A stored stamp (data.knowledge) as one: the known keys only, short
    strings; None when it isn't one."""
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

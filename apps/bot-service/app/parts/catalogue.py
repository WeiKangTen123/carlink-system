"""The parts-catalogue look-up (owner-approved 12 Nov 2026; the contract is
plans/2026-11-12-parts-catalogue/CONTRACT.md): the part numbers the online
parts catalogues list for a case's damaged parts, for the surveyor to look
at beside Annex A.

A person clicks [Look up in catalogue] on a case (app/api/catalogue.py),
or the AI reading of an intake has just been saved (maybe_auto_lookup,
called once per reading by app/intake/pipeline.py, queues one the first
time only). That queues a job; the catalogue worker (tools/parts_mcp, its
own container since 12 Nov 2026) claims it, looks each part up on the
parts sites, and sends the rows back. The API process never contacts a
parts site, and nothing here calls Gemini: it builds the job, checks the
worker's answer and keeps the rows. Only the makes the worker's sites
cover are looked up (CATALOGUE_MAKES: Honda, BMW, MINI).

What the page gets beyond the stored rows is worked out on every GET, in
code: which Annex A line a row looks like (LineMatcher), the row's name as
a new Annex A line would read (tidy_name), which rows are the same part in
another colour (variant_keys), and which diagram already shows under an
earlier damaged part (same_as_part, catalogue_view).

What the worker is told is the car (make, model, year) and the damaged
parts' names -- never the plate, VIN, chassis or engine number, the case's
id or reference, or anyone's name (job_payload). What comes back is a
reference only: it never fills the workshop's estimate, never adds an
Annex A row by itself and never prints. A person rejects rows they don't
want (and can undo that), or puts one on an Annex A line (Phase 2): the
line then carries catalogue_ref (schema.CatalogueRef), saved with PUT
/annex-a, which marks the row accepted (sync_accepted) -- and back to
suggested once no line carries it.

Off unless CATALOGUE_ENABLED is true AND CATALOGUE_WORKER_TOKEN is set
(enabled()).
"""
import hmac
import logging
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints
from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.core import as_utc, audit, display_name, utc_now
from app.auth.permissions import can_edit_case, is_draft, is_signed_off
from app.config import settings
from app.parts.history import _without_side, side_of
from app.parts.prefill import _matching_line, item_source, side_unconfirmed
from app.reports.db import SessionLocal
from app.reports.models import CatalogueJob, CatalogueSuggestion, CatalogueWorker, Report, iso_utc
from app.reports.review import checked_of
from app.reports.schema import CATALOGUE_HOSTS, CATALOGUE_TEXT_MAX, catalogue_link
from app.reports.taxonomy import CANONICAL_PARTS, OTHER_PART, UNDETERMINED_SUFFIX, other_part_name, split_side

log = logging.getLogger(__name__)

JOB_STATUSES = ("pending", "running", "done", "failed", "cancelled")
OPEN_STATUSES = ("pending", "running")
SUGGESTION_STATUSES = ("suggested", "accepted", "rejected")
# A failed job's reason, as the worker names it.
ERROR_CODES = (
    "not_supported", "no_vehicle", "needs_human", "challenge_blocked", "site_blocked", "daily_cap_reached", "internal",
)
# Why a person can't start a look-up (GET .../catalogue blocked_reason).
BLOCKED_REASONS = ("make_not_covered", "no_make_model", "no_parts", "draft", "signed_off", "readonly")
# requested_by of a job queued by itself after the AI reading (the page
# says "Started automatically after detection"). A person's job carries
# their display name.
AUTO_REQUESTED_BY = "auto (after detection)"

# A running job nobody has heard from in this long goes back to pending:
# the worker restarted or stopped half-way. A look-up of a whole case
# takes minutes, not this.
STALE_AFTER = timedelta(minutes=45)
# The worker asks for work every 20 seconds, so "online" is "asked in the
# last 2 minutes".
ONLINE_WITHIN = timedelta(minutes=2)
POLL_SECONDS = 20

# The worker's result, capped (contract "Server validation"): a bug or a
# strange page on its side mustn't fill the database.
MAX_PARTS = 40
MAX_DIAGRAMS = 3
MAX_ROWS = 250
MAX_CANDIDATES = 10
# Not in the contract, which caps everything else: one entry per site and
# step is a handful, so these are far above anything real.
MAX_FAILURES = 40
MAX_PART_FAILURES = 20
MAX_TEXT = CATALOGUE_TEXT_MAX
# The only sites the worker reads, so the only places a link in its result
# may point to (and their www. names). One list with the Annex A line's
# catalogue_ref (schema.catalogue_link).
ALLOWED_HOSTS = CATALOGUE_HOSTS


def enabled() -> bool:
    """The feature is on: the flag, and a token for the worker to use. With
    the flag on and no token the worker could never connect, so the case
    page would offer a button that queues jobs nobody takes."""
    return bool(settings.catalogue_enabled) and bool((settings.catalogue_worker_token or "").strip())


def worker_refusal(authorization: str | None) -> tuple[int, dict] | None:
    """Why a request to the worker's endpoints is refused, as (status,
    body), or None when it may go on. Asked by the sign-in middleware for
    every /catalogue-worker/ path (they carry no session cookie) and again
    by the routes themselves. 404 while the feature is off -- the same
    answer as for a path that doesn't exist -- and 401 for a missing or
    wrong token."""
    if not enabled():
        return 404, {"detail": "Not Found"}
    scheme, _, given = (authorization or "").strip().partition(" ")
    token = settings.catalogue_worker_token.strip()
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), token.encode()):
        return 401, {"detail": "Wrong or missing worker token.", "code": "worker_token"}
    return None


if settings.catalogue_enabled and not (settings.catalogue_worker_token or "").strip():
    log.warning("CATALOGUE_ENABLED is true but CATALOGUE_WORKER_TOKEN is empty: the catalogue look-up stays off")


def covered_makes() -> list[str]:
    """The makes the worker's sites cover, as CATALOGUE_MAKES writes them
    ("Honda", "BMW", "MINI"), each once."""
    out: list[str] = []
    for make in (settings.catalogue_makes or "").split(","):
        make = " ".join(make.split())
        if make and make.casefold() not in {m.casefold() for m in out}:
            out.append(make)
    return out


def make_covered(make: str | None) -> bool:
    """The case's make is one the catalogues cover, whatever its case or
    spacing ("Mini" and "MINI", " bmw ")."""
    key = " ".join(str(make or "").split()).casefold()
    return bool(key) and key in {m.casefold() for m in covered_makes()}


# ------------------------------------------------------------ the job


_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")


def _year(text) -> int | None:
    m = _YEAR.search(str(text or ""))
    if not m:
        return None
    year = int(m.group(1))
    return year if year <= utc_now().year + 1 else None


def case_year(vehicle: dict) -> int | None:
    """The car's year: vehicle_info.year, else the year it was registered,
    else None. A registration date is the year it came on the road, which
    for a parallel import can be later than it was made; the worker
    matches catalogue cars by a span of years, so either will usually do."""
    return _year(vehicle.get("year")) or _year(vehicle.get("date_of_registration"))


def model_key(model: str | None) -> str:
    """The model name's first word as the case writes it ("Vezel Hybrid"
    -> "Vezel", "HR-V 1.5" -> "HR-V"), as history._model_key takes the
    first word -- but neither lower-cased nor swapped for its twin's name:
    a twin is the worker's to find (Result.vehicle.twin_of), and a UK
    catalogue lists the HR-V, not the Vezel."""
    words = (model or "").split()
    return words[0].strip(",;") if words else ""


def _secrets(report: Report) -> list[str]:
    """What identifies the case or the people in it, as letters and digits
    only, lower-cased: none of it may reach the worker. Short values (an
    engine code like "L15B", a name like "Tan") are left out -- they could
    be part of a real model or part name -- and are never put in the job
    anyway; this only catches one typed into a part name."""
    data = report.data or {}
    vehicle = data.get("vehicle_info") or {}
    insurance = data.get("insurance_details") or {}
    values = [
        report.id, data.get("report_id"),
        vehicle.get("plate_number"), vehicle.get("vin"), vehicle.get("engine_number"),
        vehicle.get("chassis_number"), vehicle.get("driver_name"), vehicle.get("driver_contact"),
        insurance.get("policy_number"), insurance.get("claim_number"),
    ]
    out = []
    for value in values:
        key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
        if len(key) >= 5 and key not in out:
            out.append(key)
    return out


def _scrubbed(text: str, secrets: list[str]) -> str:
    """`text` without any of `secrets` in it, however it is spaced or
    hyphenated ("SXX 1234 A", "sxx-1234a")."""
    for secret in secrets:
        pattern = r"[\s\-./]*".join(re.escape(ch) for ch in secret)
        text = re.sub(pattern, " ", text, flags=re.I)
    return " ".join(text.split())


def job_parts(data: dict, secrets: list[str] = ()) -> list[dict]:
    """The damaged parts to look up, each once, in the damage list's order:
    {source_part, query, side}.

      source_part  the damage_summary part as written ("Left Tail Lamp");
                   for a named "Other / Not Listed" part, the source its
                   Annex A rows carry ("Other / Not Listed: Rear air vent",
                   prefill.item_source), so two unlisted parts stay two;
                   anything identifying typed into that name taken out
      query        what to look for: lower-case, without the side words or
                   "(side undetermined)"; a named Other by its name
      side         "left" / "right", or None -- also when the side was
                   only inferred and no person has confirmed it
                   (prefill.side_unconfirmed): the worker drops the other
                   side's rows, and a guessed side would drop the right ones

    An unnamed "Other / Not Listed" has nothing to look up and is left out.
    Cases from before damage_summary use their damaged_parts list."""
    items = [it for it in (data.get("damage_summary") or []) if isinstance(it, dict) and str(it.get("part") or "").strip()]
    if not items:
        items = [{"part": p} for p in (data.get("damaged_parts") or []) if isinstance(p, str) and p.strip()]
    out, seen = [], set()
    for it in items:
        part = str(it["part"]).strip()
        other = other_part_name(it)
        if part == OTHER_PART and not other:
            continue
        if other:
            query, side = _without_side(other), side_of(other)
        elif part in CANONICAL_PARTS:
            query, side = split_side(part)
        else:
            # Free text from before the taxonomy ("Tail lamp LH").
            query, side = _without_side(part.replace(UNDETERMINED_SUFFIX.strip(), " ")), side_of(part)
        query = _scrubbed(" ".join(query.lower().split()), list(secrets))
        # Scrubbed too: a named Other's source carries the name as typed.
        source = _scrubbed(item_source(it), list(secrets))
        if not query or source in seen:
            continue
        seen.add(source)
        if side_unconfirmed(it):
            side = None
        out.append({"source_part": source, "query": query[:MAX_TEXT], "side": side.lower() if side else None})
    return out[:MAX_PARTS]


def job_payload(report: Report, vehicle_key: str | None = None) -> dict:
    """The job as the worker gets it, without its job_id (added when it is
    claimed, job_for_worker). Nothing that identifies the case or a person:
    the car and the parts' names only."""
    data = report.data or {}
    vehicle = data.get("vehicle_info") or {}
    secrets = _secrets(report)
    brand = _scrubbed(str(vehicle.get("make") or "").strip(), secrets)[:MAX_TEXT]
    model = _scrubbed(str(vehicle.get("model") or "").strip(), secrets)[:MAX_TEXT]
    return {
        "kind": "lookup",
        "brand": brand,
        "model": model,
        "model_key": model_key(model),
        "year": case_year(vehicle),
        "vehicle_key": vehicle_key or None,
        "parts": job_parts(data, secrets),
    }


def job_for_worker(job: CatalogueJob) -> dict:
    """The claimed job as the claim answers it: the stored payload, with
    only the job's own id put in front."""
    return {"job_id": job.id, **(job.payload or {})}


def vehicle_hint(data: dict) -> dict:
    """The car as the case has it, for the panel's "Looking up: Honda
    Vezel Hybrid 2017" line. confirmed: a person confirmed both the make
    and the model in the field review (data.checked, PR #30)."""
    vehicle = (data or {}).get("vehicle_info") or {}
    checked = checked_of(data)
    marks = [checked.get("vehicle_info.make"), checked.get("vehicle_info.model")]
    return {
        "brand": (vehicle.get("make") or "").strip() or None,
        "model": (vehicle.get("model") or "").strip() or None,
        "year": case_year(vehicle),
        "confirmed": all(isinstance(m, dict) and not m.get("na") for m in marks),
    }


def blocked_reason(user, report: Report) -> str | None:
    """Why this person can't start a look-up on this case, or None. In this
    order: someone who may only look at the case ("see all" staff on
    another person's draft, anyone on a public report in the inbox: any
    viewer may change a filed case), an intake draft (never looked up: its
    damage list isn't settled; maybe_auto_lookup is the one way a draft
    gets one), a signed-off case (reopen it first), a make the catalogues
    don't cover (CATALOGUE_MAKES; said before a missing model, since adding
    the model wouldn't help), no make or model on the case (nothing to look
    the car up by), no damaged part with a name (nothing to look up:
    job_parts is empty) -- so the button is greyed with the reason instead
    of answering 400 on a click."""
    if not can_edit_case(user, report):
        return "readonly"
    if is_draft(report):
        return "draft"
    if is_signed_off(report):
        return "signed_off"
    return case_reason(report)


def case_reason(report: Report) -> str | None:
    """What in the case itself stops a look-up, whoever asks:
    make_not_covered, no_make_model or no_parts (blocked_reason's last
    three), else None."""
    vehicle = (report.data or {}).get("vehicle_info") or {}
    make, model = str(vehicle.get("make") or "").strip(), str(vehicle.get("model") or "").strip()
    if make and not make_covered(make):
        return "make_not_covered"
    if not (make and model):
        return "no_make_model"
    if not job_parts(report.data or {}, _secrets(report)):
        return "no_parts"
    return None


def not_covered_text(make: str | None) -> str:
    """"No parts catalogue for Toyota yet (Honda, BMW and MINI are
    covered)." -- the dashboard says the same (lib/catalogue.ts)."""
    makes = covered_makes()
    listed = ", ".join(makes[:-1]) + f" and {makes[-1]}" if len(makes) > 1 else "".join(makes)
    covered = f" ({listed} {'is' if len(makes) == 1 else 'are'} covered)" if makes else ""
    return f"No parts catalogue for {' '.join(str(make or '').split()) or 'this make'} yet{covered}."


# ------------------------------------------------ the worker's result


def _number_text(value):
    """A quantity or callout the site wrote as a number (2, not "2")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return value


# A link in the result: https, on one of the sites the worker reads (the
# same rule as an Annex A line's catalogue_ref).
_https_url = catalogue_link


Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TEXT)]
NumberText = Annotated[Optional[Text], BeforeValidator(_number_text)]
Url = Annotated[Optional[Text], AfterValidator(_https_url)]
# The worker sends null for a key it would have to cut (a cut key points
# elsewhere): stored as "" rather than refusing the whole result.
KeyText = Annotated[Text, BeforeValidator(lambda v: "" if v is None else v)]


class _Model(BaseModel):
    # Unknown keys are ignored, not refused: a newer worker may send more.
    model_config = ConfigDict(extra="ignore")


class CatalogueVehicle(_Model):
    key: Optional[Text] = None
    source: Optional[Text] = None
    brand: Optional[Text] = None
    model: Optional[Text] = None
    name: Optional[Text] = None
    market: Optional[Text] = None
    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)
    url: Url = None
    twin_of: Optional[Text] = None


class CatalogueRow(_Model):
    callout: NumberText = None
    part_number: Optional[Text] = None
    superseded_from: Optional[Text] = None
    name: Optional[Text] = None
    name_original: Optional[Text] = None
    qty: NumberText = None
    side: Optional[Text] = None
    notes: Optional[Text] = None
    ref_amount: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    ref_currency: Optional[Text] = None
    ref_tax_note: Optional[Text] = None
    source: Optional[Text] = None
    url: Url = None
    fetched_at: Optional[Text] = None
    stale: Optional[bool] = False


class CatalogueDiagram(_Model):
    diagram_key: KeyText = ""
    diagram_name: Optional[Text] = None
    diagram_url: Url = None
    image_url: Url = None
    match_score: Optional[float] = Field(default=None, ge=0, le=1)
    rows: list[CatalogueRow] = Field(default_factory=list, max_length=MAX_ROWS)


class CatalogueFailure(_Model):
    source: Optional[Text] = None
    code: Optional[Text] = None
    message: Optional[Text] = None


class CataloguePart(_Model):
    source_part: Text
    query: Optional[Text] = None
    side: Optional[Text] = None
    diagrams: list[CatalogueDiagram] = Field(default_factory=list, max_length=MAX_DIAGRAMS)
    failures: list[CatalogueFailure] = Field(default_factory=list, max_length=MAX_PART_FAILURES)


class WorkerResult(_Model):
    """POST /catalogue-worker/jobs/{id}/result (contract "Result")."""
    status: Literal["done", "failed"]
    error_code: Optional[Literal[ERROR_CODES]] = None
    error_text: Optional[Text] = None
    vehicle: Optional[CatalogueVehicle] = None
    candidates: list[CatalogueVehicle] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    parts: list[CataloguePart] = Field(default_factory=list, max_length=MAX_PARTS)
    failures: list[CatalogueFailure] = Field(default_factory=list, max_length=MAX_FAILURES)


# ------------------------------------------------------ the job's life


def mark_seen(name: str | None, version: str | None = None) -> None:
    """The worker asked for work or reported progress: it is online. In a
    transaction of its own, written first thing (an UPDATE, else an
    INSERT), so a seen mark never holds up or undoes the claim itself; two
    first requests from a new worker at once both end with its one row."""
    if not name:
        return
    values = {"last_seen_at": utc_now()}
    if version:
        values["version"] = version
    db = SessionLocal()
    try:
        for _ in range(2):
            changed = db.execute(
                update(CatalogueWorker).where(CatalogueWorker.name == name).values(**values)
                .execution_options(synchronize_session=False)
            ).rowcount
            if not changed:
                db.add(CatalogueWorker(name=name, **values))
            try:
                db.commit()
                return
            except IntegrityError:
                # The same worker's other request made the row a moment ago.
                db.rollback()
    finally:
        db.close()


def requeue_stale(db: Session) -> int:
    """Running jobs nobody has heard from in STALE_AFTER go back to pending,
    to be claimed afresh. Returns how many; the caller commits."""
    cutoff = utc_now() - STALE_AFTER
    result = db.execute(
        update(CatalogueJob)
        .where(CatalogueJob.status == "running")
        .where(or_(CatalogueJob.last_activity_at.is_(None), CatalogueJob.last_activity_at < cutoff))
        .values(status="pending", claimed_at=None, claimed_by=None, last_activity_at=None,
                progress_done=0, progress_message=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        log.info("Catalogue: %d stale running job(s) back to pending", result.rowcount)
    return result.rowcount or 0


def claim_next(db: Session, worker: str) -> CatalogueJob | None:
    """The oldest pending job, now running for `worker`; None when there is
    none. The status is changed by a conditional UPDATE, so two workers
    asking at once can't both get one job. A job whose case has gone since
    (deleted cases take their jobs with them, so only by a race) is
    cancelled rather than handed out. The caller commits."""
    for _ in range(10):
        job = (
            db.query(CatalogueJob)
            .filter(CatalogueJob.status == "pending")
            .order_by(CatalogueJob.created_at, CatalogueJob.id)
            .first()
        )
        if job is None:
            return None
        now = utc_now()
        if db.get(Report, job.report_id) is None:
            db.execute(update(CatalogueJob).where(CatalogueJob.id == job.id, CatalogueJob.status == "pending")
                       .values(status="cancelled", finished_at=now).execution_options(synchronize_session=False))
            continue
        taken = db.execute(
            update(CatalogueJob)
            .where(CatalogueJob.id == job.id, CatalogueJob.status == "pending")
            .values(status="running", claimed_at=now, claimed_by=worker, last_activity_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        if taken:
            db.refresh(job)
            return job
    return None


def record_progress(db: Session, job_id: int, done: int, total: int, message: str | None) -> CatalogueJob | None:
    """A running job's progress, by a conditional UPDATE (as the first
    statement, so it is a write transaction from the start). None when the
    job isn't running -- a person cancelled it, it finished, it went back
    to pending after going quiet, or its case was deleted: the worker
    should stop. The caller commits, then marks the worker seen."""
    total = max(0, total)
    done = min(max(0, done), total) if total else max(0, done)
    changed = db.execute(
        update(CatalogueJob).where(CatalogueJob.id == job_id, CatalogueJob.status == "running")
        .values(progress_done=done, progress_total=total, progress_message=message, last_activity_at=utc_now())
        .execution_options(synchronize_session=False)
    ).rowcount
    return db.get(CatalogueJob, job_id) if changed else None


def _part_number_key(value: str | None) -> str | None:
    """A part number compared without its spacing and dashes:
    "04715-T7W-A90" is "04715T7WA90"."""
    key = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return key or None


def unknown_parts(job: CatalogueJob, result: WorkerResult) -> list[str]:
    """Parts in the result the job never asked about."""
    asked = {p.get("source_part") for p in ((job.payload or {}).get("parts") or [])}
    return sorted({p.source_part for p in result.parts if p.source_part not in asked})


def finish(db: Session, job_id: int, status: str, *, error_code: str | None = None, error_text: str | None = None) -> bool:
    """Ends a running job (done / failed / cancelled). The conditional
    UPDATE is what decides a race between the worker's result and a
    person's cancel: whichever comes second finds it no longer running.
    False when it wasn't running. The caller commits."""
    values = {"status": status, "finished_at": utc_now()}
    if status != "done":
        values.update(error_code=error_code, error_text=(error_text or None) and error_text[:MAX_TEXT])
    return bool(db.execute(
        update(CatalogueJob).where(CatalogueJob.id == job_id, CatalogueJob.status == "running")
        .values(**values).execution_options(synchronize_session=False)
    ).rowcount)


def cancel(db: Session, job_id: int, by: str) -> bool:
    """A person cancels a pending or running job, by a conditional UPDATE as
    finish() ends one: a result that lands first wins, and the job stays
    done rather than showing "cancelled" over stored rows. False when it
    was no longer open. The caller commits."""
    return bool(db.execute(
        update(CatalogueJob).where(CatalogueJob.id == job_id, CatalogueJob.status.in_(OPEN_STATUSES))
        .values(status="cancelled", finished_at=utc_now(), cancelled_by=by)
        .execution_options(synchronize_session=False)
    ).rowcount)


def store_result(db: Session, job: CatalogueJob, result: WorkerResult) -> int:
    """Keeps a running job's result; returns how many rows were stored.
    The job must have been ended with finish() in the same transaction.

    Done: the case's "suggested" rows (from any earlier look-up) are
    replaced by these; rows a person accepted or rejected stay, and so does
    any row a saved Annex A line carries, whatever its status (a line must
    never point at a row that is gone: its next save would be refused); a
    new row with the same part and part number as one of them -- or as a
    row already taken from this result (the same clip on two diagrams) --
    is not added again. Failed: only the job's facts (the car, the other
    cars, the failures) are kept; the earlier suggestions stay as they
    were. The part's query and side are the job's own, not the worker's
    echo.

    The caller's finish() was the transaction's first statement, so this
    runs under SQLite's write lock: an Annex A save (which takes it before
    checking its refs, api/parts.save_annex_a) lands wholly before this or
    after it."""
    asked = {p.get("source_part"): p for p in ((job.payload or {}).get("parts") or [])}
    job.vehicle = result.vehicle.model_dump() if result.vehicle else None
    job.candidates = [c.model_dump() for c in result.candidates]
    job.failures = [f.model_dump() for f in result.failures]
    job.parts = [
        {
            "source_part": p.source_part,
            "query": asked.get(p.source_part, {}).get("query", p.query or ""),
            "side": asked.get(p.source_part, {}).get("side"),
            "diagrams": [d.model_dump(exclude={"rows"}) for d in p.diagrams],
            "failures": [f.model_dump() for f in p.failures],
        }
        for p in result.parts
    ]
    if result.status != "done":
        return 0

    replaced = delete(CatalogueSuggestion).where(
        CatalogueSuggestion.report_id == job.report_id, CatalogueSuggestion.status == "suggested",
    )
    report = db.get(Report, job.report_id)
    on_lines = _ref_ids((((report.data or {}) if report else {}).get("annex_a") or {}).get("items") or [])
    if on_lines:
        replaced = replaced.where(CatalogueSuggestion.id.notin_(sorted(on_lines)))
    db.execute(replaced.execution_options(synchronize_session=False))
    kept = {
        (s.source_part, key)
        for s in db.query(CatalogueSuggestion.source_part, CatalogueSuggestion.part_number)
        .filter(CatalogueSuggestion.report_id == job.report_id)
        for key in [_part_number_key(s.part_number)] if key
    }
    stored = 0
    for p, result_part in zip(job.parts, result.parts):
        source = p["source_part"]
        for d_rank, diagram in enumerate(result_part.diagrams):
            for r_rank, row in enumerate(diagram.rows):
                key = _part_number_key(row.part_number)
                if key and (source, key) in kept:
                    continue
                if key:
                    kept.add((source, key))
                db.add(CatalogueSuggestion(
                    report_id=job.report_id, job_id=job.id, source_part=source,
                    part_query=p["query"] or "", part_side=p["side"],
                    diagram_key=diagram.diagram_key, diagram_name=diagram.diagram_name,
                    diagram_url=diagram.diagram_url, image_url=diagram.image_url,
                    match_score=diagram.match_score, diagram_rank=d_rank, row_rank=r_rank,
                    **{**row.model_dump(), "stale": bool(row.stale)},
                ))
                stored += 1
    return stored


def forget_case(db: Session, report_id: str) -> None:
    """A deleted case's look-ups and suggestions go with it. A worker still
    busy with one of its jobs is told it was cancelled (the job no longer
    exists). The caller commits."""
    db.execute(delete(CatalogueSuggestion).where(CatalogueSuggestion.report_id == report_id)
               .execution_options(synchronize_session=False))
    db.execute(delete(CatalogueJob).where(CatalogueJob.report_id == report_id)
               .execution_options(synchronize_session=False))


# ------------------------------------------------- what the page shows


def _iso(value: datetime | None) -> str | None:
    return iso_utc(value) if value else None


def job_out(job: CatalogueJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "created_at": _iso(job.created_at),
        "claimed_at": _iso(job.claimed_at),
        "finished_at": _iso(job.finished_at),
        "requested_by": job.requested_by or None,
        "progress": {"done_parts": job.progress_done, "total_parts": job.progress_total, "message": job.progress_message},
        "error_code": job.error_code,
        "error_text": job.error_text,
        "vehicle_key": job.vehicle_key,
    }


def worker_status(db: Session) -> dict:
    """The most recently seen worker: when, and whether that is recent
    enough to call it online."""
    worker = db.query(CatalogueWorker).order_by(CatalogueWorker.last_seen_at.desc()).first()
    if worker is None:
        return {"last_seen_at": None, "online": False}
    seen = as_utc(worker.last_seen_at)
    return {"last_seen_at": iso_utc(seen), "online": utc_now() - seen <= ONLINE_WITHIN}


# Match hints: which current Annex A line a catalogue row is, by the words,
# deterministically (no AI). Catalogue names are written head first with
# shortened words ("FACE, RR. BUMPER", "LAMP ASSY, RR COMBINATION, LH",
# "Trim cover, bumper, rear"); the firm writes "Rear bumper fascia",
# "Tail lamp LH". Both are brought to one wording, then compared as a named
# unlisted part is with past lines (prefill._matching_line: the same head
# word, most of the other words).
_SHORT_WORDS = {
    "rr": "rear", "fr": "front", "frt": "front", "lwr": "lower", "upr": "upper", "otr": "outer", "inr": "inner",
    "ctr": "centre", "cntr": "centre", "brkt": "bracket", "bkt": "bracket", "mldg": "moulding", "molding": "moulding",
    "reinf": "reinforcement", "rein": "reinforcement", "garn": "garnish", "bpr": "bumper", "cvr": "cover",
    "lic": "license", "licence": "license", "l": "lh", "r": "rh", "w": "with",
}
_SAME_THING = [
    # The bumper's own skin.
    (r"\bbumper (?:trim )?(?:face|cover|fascia|panel|shell)\b", "bumper"),
    (r"\b(?:rear )?combination (?:lamp|light)\b", "tail lamp"),
    (r"\b(?:rear|tail) ?light\b", "tail lamp"),
    (r"\bhead ?light\b|\bhead lamp\b", "headlamp"),
    (r"\bfog light\b", "fog lamp"),
    (r"\b(?:door|outside|outer|side) (?:rear ?view )?mirror\b", "wing mirror"),
]


def _wording(text: str | None) -> str:
    """A part name in one wording for comparing: head-first catalogue names
    turned around ("FACE, RR. BUMPER" -> "rear bumper face"), short words
    written out, and a few names that are the same thing made one. What is
    in brackets goes first: it says which variant or what spec ("(DOT)",
    "(12V 21W)", "(KOITO)"), never what the part is -- and parts-honda.uk
    cuts long names mid-bracket ("BUMPER FACE, RR(DO", seen on the live
    HR-V page), which left a stray word that hid the line it is."""
    t = re.sub(r"\([^)]*\)?", " ", (text or "").lower())
    if "," in t:
        head, *rest = [s.strip() for s in t.split(",")]
        t = " ".join([*reversed(rest), head])
    t = " ".join(_SHORT_WORDS.get(w, w) for w in re.findall(r"[a-z0-9]+", t))
    for pattern, same in _SAME_THING:
        t = re.sub(pattern, same, t)
    return t


def _end(words: str) -> str | None:
    """front / rear, when a wording says which."""
    has = set(words.split())
    if "front" in has and "rear" not in has:
        return "front"
    if "rear" in has and "front" not in has:
        return "rear"
    return None


def _side_word(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value if value in ("left", "right") else None


NO_MATCH = {"kind": None, "line_index": None, "line_name": None}


class LineMatcher:
    """Match hints for one case's current Annex A rows, worked out once per
    GET for every suggestion on the page."""

    def __init__(self, annex_items: list[dict]):
        self.lines = []
        for index, item in enumerate(annex_items or []):
            name = str(item.get("part_name") or "").strip()
            if not name:
                continue
            words = _wording(name)
            self.lines.append({
                "name": words, "index": index, "part_name": name,
                "side": (side_of(name) or "").lower() or None, "end": _end(words),
                "source_part": item.get("source_part"),
                "number": _part_number_key(item.get("oem_part_number")),
            })

    def hint(self, s: CatalogueSuggestion) -> dict:
        """The Annex A line this row is: the line carrying its part number
        (or the number it replaced), else the line naming the same thing on
        the same side and end -- first among the lines made for the same
        damaged part, then any. {"kind": None, ...} when none is."""
        numbers = {k for k in (_part_number_key(s.part_number), _part_number_key(s.superseded_from)) if k}
        for ln in self.lines:
            if ln["number"] and ln["number"] in numbers:
                return {"kind": "line", "line_index": ln["index"], "line_name": ln["part_name"]}
        words = _wording(s.name)
        if not words:
            return dict(NO_MATCH)
        # The row's own side, else the one its name says ("BRACKET, R. RR.
        # BUMPER SIDE"), else the damaged part's.
        side = _side_word(s.side) or (side_of(words) or "").lower() or _side_word(s.part_side)
        end = _end(words) or _end(_wording(s.part_query or s.source_part))
        fitting = [
            ln for ln in self.lines
            if not (side and ln["side"] and ln["side"] != side) and not (end and ln["end"] and ln["end"] != end)
        ]
        same_part = [ln for ln in fitting if ln["source_part"] == s.source_part]
        for pool in (same_part, fitting):
            found = _matching_line(words, pool) if pool else None
            if found:
                return {"kind": "line", "line_index": found["index"], "line_name": found["part_name"]}
        return dict(NO_MATCH)


# ------------------------------------------- tidy names, colour versions
#
# tidy_name: a catalogue row's name as a new Annex A line starts out, in
# the firm's way of writing lines ("Rear bumper fascia", "Tail lamp LH",
# "Tail gate assy": taxonomy._FIRM_WORDING) -- deterministic, no AI. The
# catalogue writes the head word first, in capitals, with shortened words:
# "FACE, L. RR. BUMPER CORNE" becomes "Rear bumper corner face LH". Only
# the words in the tables below are changed; every other word is kept,
# lower-cased. The page still says "check wording" beside such a line, and
# the row's own "name" is never changed.

# Shortened words, by the word without its dot(s), lower-case.
TIDY_WORDS = {
    "rr": "rear", "fr": "front", "frt": "front",
    "lwr": "lower", "upr": "upper", "otr": "outer", "inr": "inner", "ctr": "centre", "cntr": "centre",
    "brkt": "bracket", "bkt": "bracket", "mldg": "moulding", "reinf": "reinforcement", "garn": "garnish",
    # The firm writes "assy" ("Tail gate assy", "Radiator assy").
    "assy": "assy", "sub-assy": "sub-assy", "s/a": "sub-assy",
    # Honda's "EXTENSION COMP.": the part complete (beside ASSY.).
    "comp": "complete",
    "w/": "with",
}
# A side word goes to the end, as the firm's LH / RH: "LAMP UNIT, L." ->
# "Lamp unit LH"; "L./R." (either side) as "LH/RH". Only when the name
# says one of these (two different ones: left where they are).
TIDY_SIDES = {
    "l": "LH", "lh": "LH", "l.h": "LH", "left": "LH",
    "r": "RH", "rh": "RH", "r.h": "RH", "right": "RH",
    "l./r": "LH/RH", "l/r": "LH/RH", "lh/rh": "LH/RH", "l.h./r.h": "LH/RH",
    "r./l": "LH/RH", "r/l": "LH/RH", "rh/lh": "LH/RH", "r.h./l.h": "LH/RH",
}
# parts-honda.uk cuts long names off ("FACE, L. RR. BUMPER CORNE"): the
# cut-off LAST word, when it is one of these, is written out.
TIDY_CUT_WORDS = {"corne": "corner"}
# Kept in capitals.
TIDY_ACRONYMS = frozenset({"ABS", "ACC", "DRL", "ECU", "HID", "LED", "PDC", "SRS", "TPMS", "USB"})


def _tidy_word(token: str) -> str:
    key = token.lower().rstrip(".")
    if key in TIDY_WORDS:
        return TIDY_WORDS[key]
    if key in TIDY_SIDES:
        # A side left in place (the name names two): LH / RH all the same.
        return TIDY_SIDES[key]
    if any(ch.isdigit() for ch in token):
        # A size or code: "8X25", "5MM", "M6X20", "12V" -- as written.
        return token
    if token.upper().rstrip(".") in TIDY_ACRONYMS:
        return token.upper()
    return token.lower()


def tidy_name(name: str | None) -> str | None:
    """The catalogue's name for a row, readable as an Annex A line:

      - a bracket the site cut off at the end goes ("BUMPER FACE, RR(DO"),
        and a cut-off last word in TIDY_CUT_WORDS is written out;
      - brackets that close ("(6X16)", "(KOITO)") go to the end as written;
      - head-first names are turned round, as the matcher reads them
        (_wording): "CLIP, RR. COMBINATION LIGHT" -> "rear combination
        light clip";
      - shortened words written out (TIDY_WORDS); one side word moves to
        the end as LH / RH (TIDY_SIDES);
      - sentence case: words lower-case but the first; a word with a digit
        and the TIDY_ACRONYMS as written.

    None for no name, or one that is nothing but a cut-off bracket."""
    text = " ".join(str(name or "").split())
    if not text:
        return None
    # A bracket opened and never closed: the site cut the name there.
    cut_bracket = re.search(r"\s*\([^()]*$", text)
    if cut_bracket:
        text = text[: cut_bracket.start()]
    else:
        last = re.search(r"([A-Za-z]+)\s*$", text)
        if last and last.group(1).lower() in TIDY_CUT_WORDS:
            text = text[: last.start(1)] + TIDY_CUT_WORDS[last.group(1).lower()]
    brackets = re.findall(r"\([^()]*\)", text)
    text = re.sub(r"\s*\([^()]*\)\s*", " ", text)
    segments = [s.strip() for s in text.split(",") if s.strip()]
    if len(segments) > 1:
        head, *rest = segments
        segments = [*reversed(rest), head]
    tokens = [t for t in " ".join(segments).split() if t.strip(".")]

    sides = {TIDY_SIDES[t.lower().rstrip(".")] for t in tokens if t.lower().rstrip(".") in TIDY_SIDES}
    side = sides.pop() if len(sides) == 1 else None
    words = [_tidy_word(t) for t in tokens if not (side and t.lower().rstrip(".") in TIDY_SIDES)]
    if side and not words:
        # Nothing but the side ("L."): that is the name ("LH").
        words, side = [_tidy_word(t) for t in tokens], None
    if words and words[0][:1].isalpha() and words[0] == words[0].lower():
        words[0] = words[0][:1].upper() + words[0][1:]
    out = " ".join([*words, *([side] if side else []), *brackets])
    return out[:MAX_TEXT] or None


# variant_keys: the same part in other colours. Honda lists a painted part
# once per colour, the part numbers ending in a colour code -- "Z" and a
# letter or two (04718T7JH00ZB, ...ZC, ...ZJ) -- each with the same callout,
# name and price. Folded into one row on the page ("8 colour versions"),
# the surveyor opens it and ticks the one that is on the car.
_COLOUR_SUFFIX = re.compile(r"^([A-Z0-9]{8,}?)(Z[A-Z]{1,2})?$")


def _colour_base(number: str | None) -> tuple[str, bool] | None:
    """(the part number without its colour code, whether it had one), or
    None for no number."""
    key = _part_number_key(number)
    m = _COLOUR_SUFFIX.match(key) if key else None
    return (m.group(1), bool(m.group(2))) if m else None


def variant_keys(rows: list[dict]) -> list[str | None]:
    """For one diagram's rows (suggestion_out shapes), each row's
    variant_key, or None. Rows share a key only when everything but the
    colour code matches: the part number up to a trailing Z+letter(s), and
    the callout, name, qty, side and price (amount and currency; none is
    the same as none) -- and at least two rows do, one with a colour code.
    Conservative: a near miss stays its own row."""
    groups: dict[tuple, list[tuple[int, bool]]] = {}
    for i, r in enumerate(rows):
        base = _colour_base(r.get("part_number"))
        name = " ".join(str(r.get("name") or "").split()).upper()
        if base is None or not name:
            continue
        amount = r.get("ref_amount")
        price = None if amount is None else (round(float(amount), 2), str(r.get("ref_currency") or "").strip().upper())
        signature = (base[0], str(r.get("callout") or "").strip(), name, str(r.get("qty") or "").strip(),
                     str(r.get("side") or "").strip().lower(), price)
        groups.setdefault(signature, []).append((i, base[1]))
    out: list[str | None] = [None] * len(rows)
    used: set[str] = set()
    for signature, members in groups.items():
        if len(members) < 2 or not any(coloured for _, coloured in members):
            continue
        key, n = f"{signature[0]}#{signature[1]}", 1
        while key in used:
            n += 1
            key = f"{signature[0]}#{signature[1]}#{n}"
        used.add(key)
        for i, _ in members:
            out[i] = key
    return out


def suggestion_out(s: CatalogueSuggestion, matcher: LineMatcher | None = None, variant_key: str | None = None) -> dict:
    """One row as the page shows it: the catalogue row's fields, its id and
    status, who decided it, the Annex A line it seems to be, its name as a
    new line would read (tidy_name) and its colour group (variant_key,
    worked out over its diagram's rows: catalogue_view, variant_key_of)."""
    return {
        "id": s.id,
        "callout": s.callout,
        "part_number": s.part_number,
        "superseded_from": s.superseded_from,
        "name": s.name,
        "tidy_name": tidy_name(s.name),
        "variant_key": variant_key,
        "name_original": s.name_original,
        "qty": s.qty,
        "side": s.side,
        "notes": s.notes,
        "ref_amount": s.ref_amount,
        "ref_currency": s.ref_currency,
        "ref_tax_note": s.ref_tax_note,
        "source": s.source,
        "url": s.url,
        "fetched_at": s.fetched_at,
        "stale": bool(s.stale),
        "status": s.status,
        "decided_by": s.decided_by,
        "decided_at": _iso(s.decided_at),
        "job_id": s.job_id,
        "match_hint": matcher.hint(s) if matcher else dict(NO_MATCH),
    }


def decide(s: CatalogueSuggestion, action: str, user) -> None:
    """reject: "suggested" -> "rejected"; undo: back to "suggested". The
    same action twice changes nothing. An accepted row is the Annex A
    line's now: the caller refuses both actions on it (remove the line, or
    unlink it, and save)."""
    if action == "reject" and s.status == "suggested":
        s.status, s.decided_by_id, s.decided_by, s.decided_at = "rejected", user.id, display_name(user), utc_now()
    elif action == "undo" and s.status == "rejected":
        s.status, s.decided_by_id, s.decided_by, s.decided_at = "suggested", None, None, None


# ---------------------------------- on an Annex A line (Phase 2, accept)


class BadCatalogueRef(ValueError):
    """An Annex A line's catalogue_ref names a suggestion that is not this
    case's: another case's id, or a row a newer look-up replaced while the
    line was still unsaved. `ids` are the ones not found."""

    def __init__(self, ids: list[int]):
        super().__init__(f"catalogue suggestion(s) not on this case: {ids}")
        self.ids = ids


def ref_of(s: CatalogueSuggestion) -> dict:
    """The catalogue_ref an Annex A line carries for suggestion `s`
    (schema.CatalogueRef), from the stored row."""
    return {
        "suggestion_id": s.id,
        "source": s.source,
        "part_number": s.part_number,
        "name": s.name,
        "diagram_name": s.diagram_name,
        "diagram_url": s.diagram_url,
        "ref_amount": s.ref_amount,
        "ref_currency": s.ref_currency,
        "ref_tax_note": s.ref_tax_note,
        "fetched_at": s.fetched_at,
    }


def _ref_id(row) -> int | None:
    """The suggestion id a row's catalogue_ref names, or None (no ref, or
    one without a usable id: schema.CatalogueRef never lets one in, but
    store_result reads stored rows too)."""
    ref = row.get("catalogue_ref") if isinstance(row, dict) else None
    if not isinstance(ref, dict) or isinstance(ref.get("suggestion_id"), bool):
        return None
    try:
        return int(ref.get("suggestion_id"))
    except (TypeError, ValueError):
        return None


def _ref_ids(rows: list[dict]) -> set[int]:
    return {i for i in map(_ref_id, rows) if i is not None}


def checked_refs(db: Session, report_id: str, rows: list[dict], *, drop_invalid: bool = False) -> None:
    """Each Annex A row's catalogue_ref as the catalogue has it, in place:
    the suggestion must be this case's, and its facts -- part number,
    name, diagram, the site's price -- are written from the stored row, so
    no client can make a line show a part number or price the catalogue
    didn't. Nothing here touches the estimate.

    A ref naming no suggestion of this case (another case's id, one a newer
    look-up replaced, a made-up one): BadCatalogueRef -- PUT /annex-a and
    PUT /reports/{id} refuse the save (422 catalogue_ref) -- or, with
    drop_invalid, the ref is taken off its line (None) and the rest saved,
    for the saves that must never fail over it: a new case (POST /reports,
    which has no suggestions yet), an intake draft's autosave and its
    filing."""
    for row in rows:
        if _ref_id(row) is None:
            row["catalogue_ref"] = None
    ids = _ref_ids(rows)
    if not ids:
        return
    found = {
        s.id: s
        for s in db.query(CatalogueSuggestion)
        .filter(CatalogueSuggestion.report_id == report_id, CatalogueSuggestion.id.in_(sorted(ids)))
    }
    missing = sorted(ids - set(found))
    if missing and not drop_invalid:
        raise BadCatalogueRef(missing)
    for row in rows:
        sid = _ref_id(row)
        if sid is not None:
            row["catalogue_ref"] = ref_of(found[sid]) if sid in found else None


def without_bad_refs(db: Session, report_id: str, data: dict) -> dict:
    """The case data with its Annex A refs checked as checked_refs(...,
    drop_invalid=True) does: kept and written from the stored row when the
    suggestion is this case's, taken off the line otherwise. For the saves
    that carry Annex A without being the Annex A editor's -- POST /reports,
    an intake draft's autosave (PUT /intake/{id}/data) and its filing --
    so a crafted request can't store a made-up part number or price under
    a catalogue ref, and the save itself never fails over one. Rows'
    statuses are left alone (sync_accepted is the editor's). A new list:
    `data` is not changed."""
    annex = data.get("annex_a")
    rows = annex.get("items") if isinstance(annex, dict) else None
    if not any(isinstance(it, dict) and it.get("catalogue_ref") is not None for it in rows or []):
        return data
    items = [dict(it) if isinstance(it, dict) else it for it in rows]
    checked_refs(db, report_id, [it for it in items if isinstance(it, dict)], drop_invalid=True)
    return {**data, "annex_a": {**annex, "items": items}}


def sync_accepted(db: Session, report_id: str, rows: list[dict], user) -> tuple[int, int]:
    """The case's suggestion statuses after Annex A is saved: each one a
    line carries is "accepted" (by this person, now -- unless it already
    was, also from "rejected": putting it on a line overrides hiding it);
    one that was accepted and no line carries any more is "suggested"
    again (a person may hide it or attach it afresh; the next look-up may
    replace it). Returns (newly accepted, released). The caller commits."""
    on_lines = _ref_ids(rows)
    wanted = CatalogueSuggestion.status == "accepted"
    if on_lines:
        wanted = or_(wanted, CatalogueSuggestion.id.in_(sorted(on_lines)))
    accepted = released = 0
    now = utc_now()
    for s in db.query(CatalogueSuggestion).filter(CatalogueSuggestion.report_id == report_id, wanted):
        if s.id in on_lines:
            if s.status != "accepted":
                s.status, s.decided_by_id, s.decided_by, s.decided_at = "accepted", user.id, display_name(user), now
                accepted += 1
        elif s.status == "accepted":
            s.status, s.decided_by_id, s.decided_by, s.decided_at = "suggested", None, None, None
            released += 1
    return accepted, released


def latest_job(db: Session, report_id: str, statuses=JOB_STATUSES) -> CatalogueJob | None:
    return (
        db.query(CatalogueJob)
        .filter(CatalogueJob.report_id == report_id, CatalogueJob.status.in_(statuses))
        .order_by(CatalogueJob.id.desc())
        .first()
    )


def _diagram_out(d: dict) -> dict:
    return {
        "diagram_key": d.get("diagram_key") or "",
        "diagram_name": d.get("diagram_name"),
        "diagram_url": d.get("diagram_url"),
        "image_url": d.get("image_url"),
        "match_score": d.get("match_score"),
        "same_as_part": None,
        "rows": [],
    }


def _row_identity(row: dict):
    """One catalogue row, whichever damaged part it was found for: its part
    number, else its callout and name."""
    return _part_number_key(row.get("part_number")) or ("~", row.get("callout"), row.get("name"))


def _same_diagrams(groups: dict[str, dict]) -> None:
    """A diagram that already shows under an earlier damaged part (the
    damage list's order: the latest look-up's parts first) is said once:
    each later part's entry gets same_as_part (the first part's
    source_part) and keeps only the rows the earlier parts don't show --
    none, mostly; a part of the other side keeps its own side's rows (the
    worker drops the other side's, so the Left Tail Lamp's TAILLIGHT
    diagram lists the left lamp and the Right Tail Lamp's the right one) --
    and every row a person put on a line or hid. A diagram with no key
    (the worker couldn't name it) is never taken for another."""
    shown: dict[str, dict] = {}
    for entry in groups.values():
        for key, diagram in entry["diagrams"].items():
            if not key:
                continue
            first = shown.get(key)
            if first is None:
                shown[key] = {"part": entry["source_part"], "rows": {_row_identity(r) for r in diagram["rows"]}}
                continue
            diagram["same_as_part"] = first["part"]
            diagram["rows"] = [r for r in diagram["rows"] if r["status"] != "suggested" or _row_identity(r) not in first["rows"]]
            first["rows"] |= {_row_identity(r) for r in diagram["rows"]}


def _diagram_rows_query(db: Session, report_id: str):
    """A case's rows in the page's order: newest look-up first, then each
    diagram's rank and the row's place on it."""
    return (
        db.query(CatalogueSuggestion)
        .filter(CatalogueSuggestion.report_id == report_id)
        .order_by(CatalogueSuggestion.job_id.desc(), CatalogueSuggestion.diagram_rank,
                  CatalogueSuggestion.row_rank, CatalogueSuggestion.id)
    )


def variant_key_of(db: Session, s: CatalogueSuggestion) -> str | None:
    """One row's variant_key, worked out over the rows its diagram shows
    under its damaged part (as catalogue_view does): for the answer to
    Hide / Undo, so the row stays in its colour group on the page."""
    siblings = (
        _diagram_rows_query(db, s.report_id)
        .filter(CatalogueSuggestion.source_part == s.source_part, CatalogueSuggestion.diagram_key == s.diagram_key)
        .all()
    )
    keys = variant_keys([suggestion_out(r) for r in siblings])
    return next((k for r, k in zip(siblings, keys) if r.id == s.id), None)


def catalogue_view(db: Session, report: Report, user) -> dict:
    """GET /reports/{id}/catalogue (contract "Dashboard endpoints", and
    "Phase 3" for same_as_part, tidy_name, variant_key).

    job        the case's latest look-up, whatever became of it
    vehicle    the catalogue car of the latest finished one ("done")
    candidates, failures
               of the latest look-up that ended (done or failed): a failed
               "no_vehicle" one may still list cars to pick from
    parts      the case's rows grouped by damaged part, then diagram:
               the latest done look-up's parts in its order (diagrams it
               found with no row left -- each already kept -- show empty),
               then any part only kept rows from earlier look-ups name; a
               diagram an earlier part already shows carries same_as_part
               (_same_diagrams), and each row its colour group (variant_keys)
    not_found  the latest done look-up's parts with no diagram at all
    covered_makes
               the makes the catalogues cover (CATALOGUE_MAKES), for the
               page's "No parts catalogue for Toyota yet (...)"
    """
    data = report.data or {}
    reason = blocked_reason(user, report)
    job = latest_job(db, report.id)
    done = latest_job(db, report.id, ("done",))
    ended = latest_job(db, report.id, ("done", "failed"))
    matcher = LineMatcher(((data.get("annex_a") or {}).get("items")) or [])

    groups: dict[str, dict] = {}
    for p in (done.parts or []) if done else []:
        entry = groups.setdefault(p["source_part"], {
            "source_part": p["source_part"], "query": p.get("query") or "", "side": p.get("side"),
            "diagrams": {}, "failures": list(p.get("failures") or []),
        })
        for d in p.get("diagrams") or []:
            entry["diagrams"].setdefault(d.get("diagram_key") or "", _diagram_out(d))
    for s in _diagram_rows_query(db, report.id).all():
        entry = groups.setdefault(s.source_part, {
            "source_part": s.source_part, "query": s.part_query, "side": s.part_side, "diagrams": {}, "failures": [],
        })
        diagram = entry["diagrams"].setdefault(s.diagram_key, _diagram_out({
            "diagram_key": s.diagram_key, "diagram_name": s.diagram_name, "diagram_url": s.diagram_url,
            "image_url": s.image_url, "match_score": s.match_score,
        }))
        diagram["rows"].append(suggestion_out(s, matcher))
    for entry in groups.values():
        for diagram in entry["diagrams"].values():
            for row, key in zip(diagram["rows"], variant_keys(diagram["rows"])):
                row["variant_key"] = key
    _same_diagrams(groups)

    parts = [{**entry, "diagrams": list(entry["diagrams"].values())} for entry in groups.values() if entry["diagrams"]]
    not_found = [source for source, entry in groups.items() if not entry["diagrams"]]
    return {
        "enabled": True,
        "worker": worker_status(db),
        "can_request": reason is None,
        "blocked_reason": reason,
        "vehicle_hint": vehicle_hint(data),
        "job": job_out(job),
        "vehicle": done.vehicle if done else None,
        "candidates": list((ended.candidates if ended else None) or []),
        "parts": parts,
        "not_found": not_found,
        "failures": list((ended.failures if ended else None) or []),
        "covered_makes": covered_makes(),
    }


# ----------------------------------------- by itself, after detection


def auto_lookup_reason(db: Session, report: Report) -> str | None:
    """Why no look-up is queued by itself after this case's AI reading, or
    None when one should be: the feature is off ("disabled"), the case is
    signed off, its make isn't covered or its make or model is missing or
    it has no damaged part to look up (case_reason), or it has had a
    look-up before, of any kind and however it ended ("had_job": once a
    person cancelled one or the first finished, re-readings never queue
    another -- [Look up in catalogue] is there for that)."""
    if not enabled():
        return "disabled"
    if is_signed_off(report):
        return "signed_off"
    reason = case_reason(report)
    if reason:
        return reason
    if db.query(CatalogueJob.id).filter(CatalogueJob.report_id == report.id).first() is not None:
        return "had_job"
    return None


def maybe_auto_lookup(report_id: str) -> int | None:
    """Called once the AI reading of a case is saved (app/intake/pipeline.py,
    at the end of _read_into, for a first reading and every re-reading
    alike): queues the case's first catalogue look-up, as [Look up in
    catalogue] would, when auto_lookup_reason finds nothing against it.
    requested_by AUTO_REQUESTED_BY, no person's id; the case history says
    "catalogue_lookup_requested" as for a person's, with "automatically
    after detection" in its detail.

    Returns the new job's id, else None. NEVER raises: whatever goes wrong
    here is logged and the detection that called it carries on as if this
    weren't here. In its own transaction, after the reading's own commit."""
    try:
        if not enabled():
            return None
        db = SessionLocal()
        try:
            report = db.get(Report, report_id)
            if report is None:
                return None
            reason = auto_lookup_reason(db, report)
            if reason:
                log.info("Catalogue: no look-up after detection for %s (%s)", report_id, reason)
                return None
            payload = job_payload(report)
            if not payload["parts"]:
                return None
            job = CatalogueJob(
                report_id=report.id, kind="lookup", status="pending", vehicle_key=None, payload=payload,
                requested_by_id=None, requested_by=AUTO_REQUESTED_BY, progress_total=len(payload["parts"]),
            )
            db.add(job)
            try:
                db.flush()
            except IntegrityError:
                # A person's click queued one a moment ago.
                db.rollback()
                return None
            job_id = job.id
            audit(db, None, "catalogue_lookup_requested", report.id,
                  detail=f"{len(payload['parts'])} part(s) · automatically after detection")
            db.commit()
            log.info("Catalogue: look-up %s queued after detection for %s", job_id, report_id)
            return job_id
        finally:
            db.close()
    except Exception:
        log.exception("Catalogue: the look-up after detection failed for %s (detection is unaffected)", report_id)
        return None

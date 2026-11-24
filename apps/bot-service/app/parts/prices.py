"""Stage 1: "My prices" and price safety (owner approved 24 Nov 2026;
plans/2026-11-23-report-and-parts/S1-contract.md, and the owner's final
price model of 24 Nov 2026, which overrides the contract where they differ).

Prices are personal, tailor-made per person. The price an Annex A line
gets when the machine writes it -- at filing, on "Fill prices" and when
the damage list changes (prefill.sync_annex_a) -- is chosen in this order
(choose_prices):

  1. this person's own price (UserPrice: same make, model key, line and
     side, or a side-less one): price_source "mine" -- for everyone (P3);
  2. an admin-promoted price (BookPrice "update", the active one for make
     + model key + line): "approved" -- price-book staff only;
  3. the book's median of 2+ distinct IMPORTED past cases (prefill.
     _rows_for, history.price_usable): "book" -- price-book staff only;
  4. otherwise no price: price_source None.

Steps 2 and 3 are the shared default: the firm's imported reports plus
the prices an admin promoted. Signing a case off never changes it: what
learn.py keeps of a signed-off case is history only, never a price
(history.price_usable).

Each filled-in price waits for a person (price_confirmed False) until they
type over it (the Annex A save: "typed") or press "Confirm all prices";
sign-off refuses while any is waiting (review.unconfirmed_prices). A W/shop
Est. a person types is remembered as that person's own price, per unit
(remember, DB2) -- for them alone. Admins see everyone's on "All users'
prices" (user_price_rows) and may use one for everyone (promote).

Make and model are keyed as the price book keys them (price_key): the make
as history.split_make_model writes it, the model as history.model_key
(its first word, twins merged: an HR-V is a Vezel). No AI call, nothing
read but this database.
"""
import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.parts.history import (
    LEARNED_PREFIX,
    _unit_price,
    canonical_part,
    family_of,
    is_learned,
    model_key,
    normalise_item,
    past_lines,
    price_usable,
    reduced_ref,
    side_of,
    source_ref,
    split_make_model,
    viewed_before,
)
from app.reports.models import BookPrice, PastAnnexLine, PastLabourLine, User, UserPrice, iso_utc

logger = logging.getLogger(__name__)

# The sources a person must confirm before sign-off: prices the machine
# filled in. "typed" is a person's own figure; "ai" and "catalogue" name
# where a line came from and never carry a price of their own.
FILLED_SOURCES = ("mine", "approved", "book")
PRICE_SOURCES = FILLED_SOURCES + ("typed", "ai", "catalogue")
# The fields an Annex A line carries for this (schema.AnnexAPartItem).
PRICE_FIELDS = ("price_source", "price_confirmed", "book_price", "price_date", "price_cases")
# A price the book may fill rests on this many distinct past cases (K0).
MIN_BOOK_CASES = 2
# "Old" after this (P4): shown, never blocking.
OLD_AFTER = timedelta(days=365)
MAX_UNIT_PRICE = 10_000_000.0


def _naive_utc(value: datetime | None) -> datetime | None:
    """SQLite hands DateTime columns back naive; a fresh row holds an aware one."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(value: datetime | None) -> str | None:
    return _naive_utc(value).date().isoformat() if value else None


def price_key(vehicle: dict | None) -> tuple[str, str]:
    """(make, model key) a case's prices are kept under: "Honda", "vezel"
    for a "Honda" "HR-V 1.5 RS"; ("", "") parts when either is unknown --
    then nothing is remembered or looked up. The make split as the price
    book's importer splits it (history.split_make_model), so "Mercedes-Benz"
    and "Mercedes Benz" are one make."""
    vehicle = vehicle if isinstance(vehicle, dict) else {}
    raw = " ".join(str(v).strip() for v in (vehicle.get("make"), vehicle.get("model")) if v and str(v).strip())
    make, model = split_make_model(raw)
    key = model_key(model)
    return (make, key) if make and key else ("", "")


def unit_of(row: dict) -> float:
    """A line's price for one unit: its W/shop Est. (the line total) / qty."""
    try:
        est = float(row.get("workshop_est") or 0)
        qty = float(row.get("qty") or 1.0) or 1.0
    except (TypeError, ValueError):
        return 0.0
    return round(est / qty, 2)


# ------------------------------------------------------------ the lines


def awaits_confirmation(row) -> bool:
    """A line whose price the machine filled in (mine / approved / book)
    and nobody has confirmed. Only an explicit price_confirmed False: a
    line from before Stage 1 has no such field and is never held up."""
    return (isinstance(row, dict) and row.get("price_source") in FILLED_SOURCES
            and row.get("price_confirmed") is False and bool(str(row.get("part_name") or "").strip()))


def learned_as_confirmed(row: dict) -> bool:
    """Whether a signed-off line's price is kept in its history as
    confirmed (learn.past_case_from) -- history only: no learned line ever
    prices a case (history.price_usable, the owner's final model). Its
    price_confirmed when it has one; a line from before Stage 1 by what
    the case says about it then: a past-case price nobody typed over or
    confirmed (prefilled still true) is not."""
    value = row.get("price_confirmed")
    if value is not None:
        return bool(value)
    return not row.get("prefilled")


def no_price_fields() -> dict:
    return {"price_source": None, "price_confirmed": None, "book_price": None, "price_date": None, "price_cases": None}


def _filled(row: dict, unit: float, source: str, *, date: str | None, book: float | None, cases: int | None = None) -> None:
    qty = float(row.get("qty") or 1.0) or 1.0
    row["workshop_est"] = round(float(unit) * qty, 2)
    row["prefilled"] = True
    row.update(price_source=source, price_confirmed=False, book_price=round(float(book), 2) if book else None,
               price_date=date, price_cases=cases)


def _mine(db: Session, user_id: str | None, make: str, key: str) -> dict:
    """{(item_key, side): UserPrice} of one person for one make and model."""
    if not user_id:
        return {}
    rows = (db.query(UserPrice)
            .filter(UserPrice.user_id == user_id, func.lower(UserPrice.make) == make.lower(), UserPrice.model_key == key)
            .all())
    return {(u.item_key, u.side): u for u in rows}


def _approved(db: Session, make: str, key: str) -> dict:
    """{item_key: BookPrice} -- the active "update" per line of one make
    and model. Inside history.restricted (the AI accuracy record), none
    decided at or after its cut-off: the machine's Annex A as it was when
    the case was filed."""
    q = db.query(BookPrice).filter(func.lower(BookPrice.make) == make.lower(), BookPrice.model_key == key,
                                   BookPrice.decision == "update", BookPrice.active.is_(True))
    before = viewed_before()
    out: dict[str, BookPrice] = {}
    for d in q.order_by(BookPrice.decided_at, BookPrice.id).all():
        if before is not None and _naive_utc(d.decided_at) >= before:
            continue
        out[d.item_key] = d  # the newest last
    return out


def choose_prices(db: Session, rows: list[dict], vehicle: dict, *, user_id: str | None, price_book: bool) -> None:
    """Steps 1 and 2 of the price choice (this module's docstring) over the
    Annex A rows the machine has just built (prefill.sync_annex_a's new
    rows); step 3 is already on them (prefill._rows_for: "book"). In place.

    user_id: whose own prices may fill them -- None for none (the caller
    decides: never a person's prices into a case whose owner may not see
    them, api/parts.own_prices_for_case). price_book: steps 2 and 3, and
    the book's figure beside a person's own (book_price) -- the firm's
    price book, staff only."""
    make, key = price_key(vehicle)
    if not make:
        return
    mine = _mine(db, user_id, make, key)
    approved = _approved(db, make, key) if price_book else {}
    if not mine and not approved:
        return
    for row in rows:
        name = str(row.get("part_name") or "").strip()
        if not name:
            continue
        item, side = normalise_item(name), side_of(name)
        own = mine.get((item, side)) or mine.get((item, None))
        decided = approved.get(item)
        book = row.get("book_price") if row.get("price_source") == "book" else None
        if own is not None:
            reference = (decided.unit_price if decided is not None else book) if price_book else None
            _filled(row, own.unit_price, "mine", date=_iso_date(own.updated_at), book=reference)
        elif decided is not None:
            _filled(row, decided.unit_price, "approved", date=_iso_date(decided.decided_at), book=decided.unit_price)


# ------------------------------------------------------------ remember


def price_dict(u: UserPrice) -> dict:
    """A person's own price as GET /me/prices lists it (and PATCH answers)."""
    updated = _naive_utc(u.updated_at)
    return {
        "id": u.id,
        "make": u.make,
        "model_key": u.model_key,
        "wording": u.wording,
        "side": u.side,
        "unit_price": u.unit_price,
        "updated_at": iso_utc(u.updated_at) if u.updated_at else None,
        "uses": u.uses,
        "last_case_id": u.last_case_id,
        "old": bool(updated and _naive_utc(_now()) - updated > OLD_AFTER),
    }


def _same_key(db: Session, user_id: str, make: str, key: str, item: str, side: str | None) -> UserPrice | None:
    q = db.query(UserPrice).filter(UserPrice.user_id == user_id, func.lower(UserPrice.make) == make.lower(),
                                   UserPrice.model_key == key, UserPrice.item_key == item)
    q = q.filter(UserPrice.side.is_(None)) if side is None else q.filter(UserPrice.side == side)
    return q.first()


def remember(db: Session, user_id: str | None, vehicle: dict, case_id: str | None, lines) -> list[dict]:
    """Keeps the W/shop Est. a person typed as their own price (P1, DB2):
    one row per make, model key, line and side, its unit price est / qty.
    lines: (wording, est, qty) per line whose estimate the person changed
    on this save. A row this save made has no previous price (Undo removes
    it); one it changed keeps the price before the save for Undo. The
    caller commits. Answers the rows made or changed, as the Annex A save
    returns them: [{id, wording, make, model_key, unit_price,
    previous_unit_price}] -- none when the case's make or model is
    unknown, nor for an estimate cleared to 0."""
    make, key = price_key(vehicle)
    if not user_id or not make:
        return []
    now = _now()
    made: set[int] = set()
    touched: set[int] = set()
    out: dict[int, dict] = {}
    for wording, est, qty in lines:
        wording = " ".join(str(wording or "").split())[:200]
        item = normalise_item(wording)
        try:
            est, qty = float(est or 0), float(qty or 1.0) or 1.0
        except (TypeError, ValueError):
            continue
        if not item or est <= 0:
            continue
        unit = round(est / qty, 2)
        if unit <= 0:
            # Under a cent a unit (0.01 for 10): kept as 0, it would fill the
            # next case with an estimate of 0 still waiting (review Low 2).
            continue
        side = side_of(wording)
        row = _same_key(db, user_id, make, key, item, side)
        if row is None:
            row = UserPrice(user_id=user_id, make=make, model_key=key, item_key=item, family=family_of(wording), side=side,
                            wording=wording, unit_price=unit, previous_unit_price=None, last_case_id=case_id, uses=1,
                            created_at=now, updated_at=now)
            db.add(row)
            db.flush()
            made.add(row.id)
            touched.add(row.id)
            changed = True
        else:
            changed = abs(float(row.unit_price) - unit) >= 0.005
            if row.id not in touched:
                touched.add(row.id)
                row.uses = int(row.uses or 0) + 1
                if changed:
                    row.previous_unit_price = row.unit_price
            row.unit_price, row.wording, row.family = unit, wording, family_of(wording)
            row.last_case_id, row.updated_at = case_id, now
        if changed or row.id in made:
            out[row.id] = {"id": row.id, "wording": row.wording, "make": row.make, "model_key": row.model_key,
                           "unit_price": row.unit_price,
                           "previous_unit_price": None if row.id in made else row.previous_unit_price}
    return list(out.values())


def set_price(row: UserPrice, unit: float) -> float:
    """PATCH /me/prices/{id}: a new unit price, the old one kept for Undo.
    Returns the old one."""
    old = row.unit_price
    row.previous_unit_price, row.unit_price = old, round(float(unit), 2)
    row.updated_at = _now()
    return old


def undo(db: Session, row: UserPrice) -> str:
    """POST /me/prices/{id}/undo: the price before the last change back,
    or the row gone when that change made it (no price before). Returns
    "restored", "removed", or "unchanged" when there is nothing to undo
    (undone already: the price before is the price now). The caller
    audits and commits."""
    previous = row.previous_unit_price
    if previous is None:
        db.delete(row)
        return "removed"
    if abs(float(previous) - float(row.unit_price)) < 0.005:
        return "unchanged"
    # previous_unit_price stays, equal to the price now: a second Undo
    # does nothing (it never removes a row that wasn't new).
    row.unit_price = previous
    row.uses = max(1, int(row.uses or 1) - 1)
    row.updated_at = _now()
    return "restored"


def label(make: str, key: str, wording: str) -> str:
    """"Honda vezel · Rear bumper fascia" for a history entry: the car and
    the line, never the case or a person."""
    return f"{make} {key} · {wording}"[:200]


# ---------------------------------------------------------------- the book


def _book_lines(db: Session) -> dict[tuple[str, str, str], list]:
    """The paid past lines of the firm's imported reports (history.
    price_usable: never a line learned from a Carlink case) by (make
    lowered, model key, item_key): the book as the prefill reads it."""
    out: dict[tuple[str, str, str], list] = {}
    for ln in past_lines(db):
        if not ln.make or not ln.item_key or not price_usable(ln) or _unit_price(ln) is None:
            continue
        out.setdefault((ln.make.lower(), model_key(ln.model), ln.item_key), []).append(ln)
    return out


def _reference(lines: list | None) -> tuple[float | None, int]:
    """(the book's unit W/shop Est. -- the median of est / qty over the
    paid lines, as prefill fills it -- and the distinct cases behind it)
    for one line of one make and model."""
    if not lines:
        return None, 0
    ests = [round(ln.workshop_est / (ln.qty or 1.0), 2) for ln in lines if ln.workshop_est and ln.workshop_est > 0]
    cases = len({ln.case_ref for ln in lines})
    return (round(statistics.median(ests), 2) if ests else None), cases


def _promoted(db: Session) -> dict[tuple[str, str, str], BookPrice]:
    """The active admin-promoted price ("update") per (make lowered, model
    key, item_key), the newest last."""
    out: dict[tuple[str, str, str], BookPrice] = {}
    q = db.query(BookPrice).filter(BookPrice.decision == "update", BookPrice.active.is_(True))
    for d in q.order_by(BookPrice.decided_at, BookPrice.id).all():
        out[(d.make.lower(), d.model_key, d.item_key)] = d
    return out


class Defaults:
    """The shared default for any line, worked out once per request: an
    admin-promoted price, else the book's median of 2+ distinct imported
    cases, else none."""

    def __init__(self, db: Session):
        self._book = _book_lines(db)
        self._promoted = _promoted(db)

    def of(self, make: str, key: str, item: str) -> tuple[float | None, str | None]:
        """(unit price, "approved" | "book") or (None, None)."""
        group = (str(make or "").lower(), key, item)
        decided = self._promoted.get(group)
        if decided is not None and decided.unit_price:
            return round(float(decided.unit_price), 2), "approved"
        median, cases = _reference(self._book.get(group))
        if median and cases >= MIN_BOOK_CASES:
            return median, "book"
        return None, None


def user_name(user) -> str | None:
    """How an account is named to admins (auth.core.display_name); None
    for none."""
    from app.auth.core import display_name

    return display_name(user) if user is not None else None


def _names(db: Session, user_ids) -> dict:
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    return {u.id: user_name(u) for u in db.query(User).filter(User.id.in_(ids)).all()}


def user_price_row(u: UserPrice, defaults: Defaults, names: dict) -> dict:
    """One person's price as the admins' list shows it (GET /admin/user-
    prices): who (user_name None for an account that is gone), the car and
    the line -- part: the standard name where one is known (history.
    canonical_part), wording: as it prints -- their unit price, and the
    shared default beside it (default_source "approved" | "book" | None)."""
    default, source = defaults.of(u.make, u.model_key, u.item_key)
    return {
        "id": u.id,
        "user_id": u.user_id,
        "user_name": names.get(u.user_id),
        "make": u.make,
        "model_key": u.model_key,
        "item_key": u.item_key,
        "part": canonical_part(u.wording) if u.wording else None,
        "wording": u.wording,
        "side": u.side,
        "unit_price": u.unit_price,
        "default_price": default,
        "default_source": source,
        "updated_at": iso_utc(u.updated_at) if u.updated_at else None,
        "uses": u.uses,
        "last_case_id": u.last_case_id,
    }


def user_price_rows(db: Session, *, user: str = "", make: str = "", model: str = "", q: str = "") -> list[dict]:
    """GET /admin/user-prices: every person's own prices, newest first.
    user: an account's id, or words of its name or username (any letter
    case); make: the make, any case ("Mercedes-Benz" is "Mercedes Benz");
    model: as typed on a case ("HR-V" finds the Vezel's); q: words in the
    wording, the standard part name, the make or the model."""
    query = db.query(UserPrice)
    who = user.strip()
    if who:
        needle = who.lower()
        ids = [u.id for u in db.query(User).all()
               if u.id == who or needle in (u.name or "").lower() or needle in (u.username or "").lower()]
        query = query.filter(UserPrice.user_id.in_(ids))
    if make.strip():
        wanted = split_make_model(make)[0] or " ".join(make.split())
        query = query.filter(func.lower(UserPrice.make) == wanted.lower())
    if model.strip():
        query = query.filter(UserPrice.model_key == model_key(model))
    rows = query.order_by(UserPrice.updated_at.desc(), UserPrice.id.desc()).all()
    defaults = Defaults(db)
    names = _names(db, [u.user_id for u in rows])
    out = [user_price_row(u, defaults, names) for u in rows]
    words = " ".join(q.split()).lower()
    if words:
        out = [r for r in out if any(words in str(r.get(k) or "").lower() for k in ("wording", "part", "make", "model_key"))]
    return out


def promote(db: Session, admin, row: UserPrice, unit_price: float | None, reason: str = "") -> tuple[BookPrice, float | None, str | None]:
    """POST /admin/user-prices/{id}/promote, "Use this price for everyone":
    the shared default for that make, model and line becomes unit_price
    (the person's own price when None) -- a BookPrice "update", active;
    every older row on the key inactive. Answers (the row, the default
    before and its source). The caller audits and commits."""
    unit = round(float(row.unit_price if unit_price is None else unit_price), 2)
    before, source = Defaults(db).of(row.make, row.model_key, row.item_key)
    older = db.query(BookPrice).filter(func.lower(BookPrice.make) == row.make.lower(), BookPrice.model_key == row.model_key,
                                       BookPrice.item_key == row.item_key, BookPrice.active.is_(True))
    for d in older.all():
        d.active = False
    made = BookPrice(
        make=row.make, model_key=row.model_key, item_key=row.item_key, family=row.family or family_of(row.wording or ""),
        wording=row.wording or row.item_key, decision="update", unit_price=unit,
        book_price_before=round(before, 2) if before else None, users_count=1, cases_count=1 if row.last_case_id else 0,
        decided_by=admin.id, decided_at=_now(), reason=(reason or "").strip()[:500], active=True,
    )
    db.add(made)
    db.flush()
    return made, before, source


# ----------------------------------------------------- start-up steps (db._once)


def mark_past_line_origins(db: Session) -> int:
    """upgrade:price_book_origin_v1: every past line gets its origin from
    its case reference -- "carlink:..." a signed-off Carlink case, anything
    else an imported spreadsheet -- and the imported ones are confirmed.
    Lines learned before Stage 1 stay unconfirmed (confirmed None); no
    learned line ever prices a case anyway (history.price_usable). Returns
    the lines given an origin; the caller (db._once) commits."""
    carlink = (db.query(PastAnnexLine)
               .filter(PastAnnexLine.origin.is_(None), PastAnnexLine.case_ref.startswith(LEARNED_PREFIX))
               .update({PastAnnexLine.origin: "carlink"}, synchronize_session=False))
    imported = (db.query(PastAnnexLine).filter(PastAnnexLine.origin.is_(None))
                .update({PastAnnexLine.origin: "import", PastAnnexLine.confirmed: True}, synchronize_session=False))
    return carlink + imported


def scrub_price_book(db: Session) -> dict:
    """upgrade:price_book_personal_v1 (owner K0, irreversible; the deploy's
    backup keeps the database as it was; Stage 1 review H1): no plate left
    in the price book, in either past table --
      - plate blanked;
      - source_file cut down to its CL reference ("CL 10001" of "CL 10001
        TEST 123 X.xlsx", history.source_ref), or blanked when it has none:
        the firm names its files with the plate;
      - an imported case's reference that is a file name the importer fell
        back to, cut down to its CL reference likewise (history.reduced_ref;
        a reference that is a CL reference alone stays as written).
    Idempotent: run again it finds nothing. One history entry with the
    counts -- never a plate, a file name or a reference. Returns the counts;
    the caller (db._once) commits."""
    from app.auth.core import audit

    counts = {"plate_parts": 0, "plate_labour": 0, "file_parts": 0, "file_labour": 0, "refs": 0}
    for model, tag in ((PastAnnexLine, "parts"), (PastLabourLine, "labour")):
        counts[f"plate_{tag}"] = (db.query(model).filter(model.plate.isnot(None), model.plate != "")
                                  .update({model.plate: ""}, synchronize_session=False))
        for (value,) in db.query(model.source_file).filter(model.source_file.isnot(None), model.source_file != "").distinct().all():
            new = source_ref(value)
            if new != value:
                counts[f"file_{tag}"] += (db.query(model).filter(model.source_file == value)
                                          .update({model.source_file: new}, synchronize_session=False))
        for (value,) in db.query(model.case_ref).distinct().all():
            if not value or is_learned(value):
                continue
            new = reduced_ref(value)
            if new != value:
                counts["refs"] += (db.query(model).filter(model.case_ref == value)
                                   .update({model.case_ref: new}, synchronize_session=False))
    plates = counts["plate_parts"] + counts["plate_labour"]
    files = counts["file_parts"] + counts["file_labour"]
    audit(db, None, "price_book_personal_data_dropped",
          detail=(f"Plates removed from the price book: {plates} line(s) ({counts['plate_parts']} part, "
                  f"{counts['plate_labour']} labour). File names cut to the CL reference: {files} line(s) "
                  f"({counts['file_parts']} part, {counts['file_labour']} labour). "
                  f"References cut to the CL reference: {counts['refs']} line(s)."))
    logger.info("Price book: %d plate(s), %d file name(s), %d reference(s) removed", plates, files, counts["refs"])
    return counts

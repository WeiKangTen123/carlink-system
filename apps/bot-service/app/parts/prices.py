"""Stage 1: "My prices" and price safety (owner approved 24 Nov 2026;
plans/2026-11-23-report-and-parts/S1-contract.md).

The price an Annex A line gets when the machine writes it -- at filing,
on "Fill prices" and when the damage list changes (prefill.sync_annex_a)
-- is chosen in this order (choose_prices):

  1. this person's own price (UserPrice: same make, model key, line and
     side, or a side-less one): price_source "mine" -- for everyone (P3);
  2. an admin-approved price (BookPrice "update", the active one for make
     + model key + line): "approved" -- price-book staff only;
  3. the book's median of confirmed past lines from 2+ distinct cases
     (prefill._rows_for, history.price_usable): "book" -- price-book
     staff only, as before;
  4. otherwise no price: price_source None.

Each such price waits for a person (price_confirmed False) until they
type over it (the Annex A save: "typed") or press "Confirm all prices";
sign-off refuses while any is waiting (review.unconfirmed_prices). A W/shop
Est. a person types is remembered as that person's own price, per unit
(remember, DB2). The shared book changes only when an admin decides on the
"Price differences" list (differences / decide, P1).

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
    family_of,
    model_key,
    normalise_item,
    past_lines,
    price_usable,
    side_of,
    split_make_model,
    viewed_before,
)
from app.reports.models import BookPrice, PastAnnexLine, PastLabourLine, UserPrice, iso_utc

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
# A person's price this much off the book's is on the admins' list.
DIFFERENCE = 0.10
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
    """Whether a signed-off line's price joins the book as confirmed
    (learn.past_case_from; only those ever price a case, DB4). Its
    price_confirmed when it has one; a line from before Stage 1 by what
    the case says about it then: a past-case price nobody typed over or
    confirmed (prefilled still true) is not -- the owner's K0, learn only
    confirmed prices."""
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
    """The confirmed, paid past lines (history.price_usable) by (make
    lowered, model key, item_key): the book the admins' list compares
    with, as the prefill reads it (history.past_lines: a learned case only
    while it is signed off and learning is on)."""
    out: dict[tuple[str, str, str], list] = {}
    for ln in past_lines(db):
        if not ln.make or not ln.item_key or not price_usable(ln) or _unit_price(ln) is None:
            continue
        out.setdefault((ln.make.lower(), model_key(ln.model), ln.item_key), []).append(ln)
    return out


def _reference(lines: list | None) -> tuple[float | None, int, str]:
    """(the book's unit W/shop Est. -- the median of est / qty over the
    paid lines, as prefill fills it -- the distinct cases behind it, the
    most common wording) for one line of one make and model."""
    if not lines:
        return None, 0, ""
    ests = [round(ln.workshop_est / (ln.qty or 1.0), 2) for ln in lines if ln.workshop_est and ln.workshop_est > 0]
    names = [ln.item for ln in lines]
    wording = max(sorted(set(names)), key=names.count)
    cases = len({ln.case_ref for ln in lines})
    return (round(statistics.median(ests), 2) if ests else None), cases, wording


def _decisions(db: Session) -> tuple[dict, dict]:
    """({key: the active "update"}, {key: when the active "keep" was
    made}), keys as _book_lines'."""
    approved: dict[tuple, BookPrice] = {}
    kept: dict[tuple, datetime] = {}
    for d in db.query(BookPrice).filter(BookPrice.active.is_(True)).order_by(BookPrice.decided_at, BookPrice.id).all():
        key = (d.make.lower(), d.model_key, d.item_key)
        if d.decision == "update":
            approved[key] = d
        elif d.decision == "keep":
            kept[key] = _naive_utc(d.decided_at)
    return approved, kept


def _most_common(words: list[str]) -> str:
    words = [w for w in words if w]
    return max(sorted(set(words)), key=words.count) if words else ""


def _group_counts(rows: list[UserPrice]) -> tuple[int, int]:
    return len({u.user_id for u in rows}), len({u.last_case_id for u in rows if u.last_case_id})


def differences(db: Session) -> list[dict]:
    """GET /admin/price-differences: per make, model key and line, where
    at least one person's own price is more than 10% off the book's
    reference -- the active approved price, else the median of confirmed
    past lines from 2+ distinct cases -- and no active "keep" decision is
    newer than every such price. Counts and prices only: never who."""
    groups: dict[tuple, list[UserPrice]] = {}
    for u in db.query(UserPrice).all():
        groups.setdefault((u.make.lower(), u.model_key, u.item_key), []).append(u)
    if not groups:
        return []
    book = _book_lines(db)
    approved, kept = _decisions(db)
    out = []
    for key, rows in groups.items():
        decided = approved.get(key)
        median, cases, _ = _reference(book.get(key))
        reference = decided.unit_price if decided is not None else (median if cases >= MIN_BOOK_CASES else None)
        if not reference or reference <= 0:
            continue
        off = [u for u in rows if abs(float(u.unit_price) - reference) > DIFFERENCE * reference]
        if not off:
            continue
        keep_at = kept.get(key)
        if keep_at is not None and all(_naive_utc(u.updated_at) <= keep_at for u in off):
            continue
        units = [float(u.unit_price) for u in rows]
        users, case_count = _group_counts(rows)
        last = max(rows, key=lambda u: _naive_utc(u.updated_at) or datetime.min)
        out.append({
            "make": rows[0].make,
            "model_key": key[1],
            "item_key": key[2],
            "wording": _most_common([u.wording for u in rows]),
            "book_price": round(reference, 2),
            "book_cases": cases,
            "approved": decided is not None,
            "users_count": users,
            "cases_count": case_count,
            "min_price": round(min(units), 2),
            "max_price": round(max(units), 2),
            "median_user_price": round(statistics.median(units), 2),
            "last_used": iso_utc(last.updated_at) if last.updated_at else None,
        })
    out.sort(key=lambda g: (-g["users_count"], -g["cases_count"], g["make"].lower(), g["model_key"], g["wording"].lower()))
    return out


def decide(db: Session, admin, *, make: str, key: str, item: str, decision: str, unit_price: float | None,
           reason: str = "") -> BookPrice:
    """POST /admin/price-differences/decide: one BookPrice row. "update"
    sets the book's unit price for the line (every older decision on it
    inactive); "keep" leaves the book as it is (older "keep"s inactive;
    an approved price stays the book's). The caller audits and commits."""
    make, key, item = " ".join(make.split()), key.strip().lower(), " ".join(item.split()).lower()
    group_key = (make.lower(), key, item)
    rows = [u for u in db.query(UserPrice).filter(func.lower(UserPrice.make) == make.lower(), UserPrice.model_key == key,
                                                   UserPrice.item_key == item).all()]
    approved, _ = _decisions(db)
    decided = approved.get(group_key)
    median, cases, book_wording = _reference(_book_lines(db).get(group_key))
    before = decided.unit_price if decided is not None else (median if cases >= MIN_BOOK_CASES else None)
    wording = _most_common([u.wording for u in rows]) or book_wording or item
    older = db.query(BookPrice).filter(func.lower(BookPrice.make) == make.lower(), BookPrice.model_key == key,
                                       BookPrice.item_key == item, BookPrice.active.is_(True))
    if decision == "keep":
        older = older.filter(BookPrice.decision == "keep")
    for d in older.all():
        d.active = False
    users, case_count = _group_counts(rows)
    row = BookPrice(
        make=rows[0].make if rows else make, model_key=key, item_key=item, family=family_of(wording), wording=wording,
        decision=decision, unit_price=round(float(unit_price), 2) if decision == "update" else None,
        book_price_before=round(before, 2) if before else None, users_count=users, cases_count=case_count,
        decided_by=admin.id, decided_at=_now(), reason=(reason or "").strip()[:500], active=True,
    )
    db.add(row)
    return row


# ----------------------------------------------------- start-up steps (db._once)


def mark_past_line_origins(db: Session) -> int:
    """upgrade:price_book_origin_v1: every past line gets its origin from
    its case reference -- "carlink:..." a signed-off Carlink case, anything
    else an imported spreadsheet -- and the imported ones are confirmed.
    Lines learned before Stage 1 stay unconfirmed (confirmed None): nobody
    confirmed their prices, so they never price a case (DB4). Returns the
    lines given an origin; the caller (db._once) commits."""
    carlink = (db.query(PastAnnexLine)
               .filter(PastAnnexLine.origin.is_(None), PastAnnexLine.case_ref.startswith(LEARNED_PREFIX))
               .update({PastAnnexLine.origin: "carlink"}, synchronize_session=False))
    imported = (db.query(PastAnnexLine).filter(PastAnnexLine.origin.is_(None))
                .update({PastAnnexLine.origin: "import", PastAnnexLine.confirmed: True}, synchronize_session=False))
    return carlink + imported


def drop_price_book_plates(db: Session) -> int:
    """upgrade:price_book_plates_v1 (owner K0, irreversible; the deploy's
    backup keeps the database as it was): the plate blanked on every past
    part and labour line. One history entry says how many -- never a
    plate. Returns the count; the caller (db._once) commits."""
    from app.auth.core import audit

    parts = (db.query(PastAnnexLine).filter(PastAnnexLine.plate.isnot(None), PastAnnexLine.plate != "")
             .update({PastAnnexLine.plate: ""}, synchronize_session=False))
    labour = (db.query(PastLabourLine).filter(PastLabourLine.plate.isnot(None), PastLabourLine.plate != "")
              .update({PastLabourLine.plate: ""}, synchronize_session=False))
    audit(db, None, "price_book_plates_dropped",
          detail=f"Plates removed from the price book: {parts + labour} line(s) ({parts} part, {labour} labour).")
    return parts + labour

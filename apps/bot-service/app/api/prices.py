"""Stage 1: "My prices", confirming prices, and the admins' "Price
differences" (owner approved 24 Nov 2026; plans/2026-11-23-report-and-
parts/S1-contract.md; the logic is app/parts/prices.py).

    GET    /me/prices                            this person's own prices (?make=&model=&q=)
    PATCH  /me/prices/{id}                       {unit_price} -> the row
    DELETE /me/prices/{id}                       a history entry with the old price (DB3)
    POST   /me/prices/{id}/undo                  the price before the last change back
    POST   /reports/{id}/annex-a/confirm-prices  every Annex A line's price confirmed
    GET    /admin/price-differences              admin: where people's prices differ from the book
    POST   /admin/price-differences/decide       admin: update the book's price, or keep it

"My prices" are every signed-in account's own (owner P3), never anyone
else's: another person's row is 404, the same as one that doesn't exist.
The admins' list shows counts and prices, never who.
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func

from app.api.annexes import _editable
from app.auth.core import CurrentUser, audit
from app.auth.deps import ApiError, current_user, editable_case, require_admin
from app.parts import prices
from app.parts.history import model_key, split_make_model
from app.reports.db import SessionLocal
from app.reports.models import UserPrice
from app.reports.synthesize_annexes import synthesize_annexes_for_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prices"])

NOT_FOUND = "No such price of yours."


class UnitPrice(BaseModel):
    unit_price: float = Field(gt=0, le=prices.MAX_UNIT_PRICE)


def label_of(row: UserPrice) -> str:
    return prices.label(row.make, row.model_key, row.wording)


def _own(db, user: CurrentUser, price_id: int) -> UserPrice:
    row = db.get(UserPrice, price_id)
    if row is None or row.user_id != user.id:
        raise ApiError(404, NOT_FOUND, "price_not_found")
    return row


# ------------------------------------------------------------ My prices


@router.get("/me/prices")
def my_prices(make: str = "", model: str = "", q: str = "", user: CurrentUser = Depends(current_user)) -> dict:
    """This person's own prices, by make, model and wording. make: the
    make, any letter case; model: as typed on a case ("HR-V" finds the
    Vezel's, history.model_key); q: words in the wording. old: last changed
    over 12 months ago (P4) -- shown, never blocking."""
    db = SessionLocal()
    try:
        query = db.query(UserPrice).filter(UserPrice.user_id == user.id)
        if make.strip():
            # As the prices are kept (prices.price_key): "Mercedes-Benz" is
            # "Mercedes Benz".
            wanted = split_make_model(make)[0] or " ".join(make.split())
            query = query.filter(func.lower(UserPrice.make) == wanted.lower())
        if model.strip():
            query = query.filter(UserPrice.model_key == model_key(model))
        if q.strip():
            query = query.filter(func.lower(UserPrice.wording).contains(q.strip().lower(), autoescape=True))
        rows = query.order_by(func.lower(UserPrice.make), UserPrice.model_key, func.lower(UserPrice.wording), UserPrice.id).all()
        return {"prices": [prices.price_dict(u) for u in rows]}
    finally:
        db.close()


@router.patch("/me/prices/{price_id}")
def change_my_price(price_id: int, body: UnitPrice, user: CurrentUser = Depends(current_user)) -> dict:
    """A new unit price for one of this person's own prices; the old one is
    kept for Undo. Answers the row as GET /me/prices lists it."""
    db = SessionLocal()
    try:
        row = _own(db, user, price_id)
        old = prices.set_price(row, body.unit_price)
        audit(db, user, "my_price_changed", detail=f"{label_of(row)}: {old:,.2f} -> {row.unit_price:,.2f}")
        db.commit()
        return prices.price_dict(row)
    finally:
        db.close()


@router.delete("/me/prices/{price_id}")
def delete_my_price(price_id: int, user: CurrentUser = Depends(current_user)) -> dict:
    """Removes one of this person's own prices, with a history entry
    saying which line and the price it had (DB3; the entry has who and
    when)."""
    db = SessionLocal()
    try:
        row = _own(db, user, price_id)
        audit(db, user, "my_price_deleted", detail=f"{label_of(row)}: was {row.unit_price:,.2f}")
        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/me/prices/{price_id}/undo")
def undo_my_price(price_id: int, user: CurrentUser = Depends(current_user)) -> dict:
    """The toast's [Undo] after an Annex A save remembered a price: the
    price before back, or the row removed when that save made it
    (prices.undo), with a history entry; undone already: nothing."""
    db = SessionLocal()
    try:
        row = _own(db, user, price_id)
        what, was = label_of(row), row.unit_price
        done = prices.undo(db, row)
        if done != "unchanged":
            detail = f"{what}: removed (was {was:,.2f})" if done == "removed" else f"{what}: {was:,.2f} -> {row.unit_price:,.2f}"
            audit(db, user, "my_price_undone", detail=detail)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ------------------------------------------------------- Confirm all prices


@router.post("/reports/{report_id}/annex-a/confirm-prices")
def confirm_prices(report_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    """"Confirm all prices" (owner P5): every Annex A line's price is
    confirmed (price_confirmed true) -- the person has checked them against
    the workshop's quotation. As every Annex A change: editable_case, 409
    for a draft or a signed-off case. A history entry says how many were
    waiting. Answers {annex_a, annex_c} as the Annex A save (PUT
    /reports/{id}/annex-a) does, and how many it confirmed (confirmed)."""
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        data = dict(r.data or {})
        annex = dict(data.get("annex_a") or {})
        items, waiting = [], 0
        for row in annex.get("items") or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            if prices.awaits_confirmation(row):
                waiting += 1
            if str(row.get("part_name") or "").strip():
                row["price_confirmed"] = True
            items.append(row)
        annex["items"] = items
        data["annex_a"] = annex
        # The totals as every Annex A save works them out (no figure moved).
        data = synthesize_annexes_for_report(data)
        r.data = data
        audit(db, user, "annex_a_prices_confirmed", r.id,
              detail=f"{waiting} price{'s' if waiting != 1 else ''} confirmed ({len(items)} line(s))")
        db.commit()
        return {"annex_a": data["annex_a"], "annex_c": data["annex_c"], "confirmed": waiting}
    finally:
        db.close()


# ------------------------------------------------------ Price differences


@router.get("/admin/price-differences")
def price_differences(admin: CurrentUser = Depends(require_admin)) -> dict:
    """{groups: [{make, model_key, item_key, wording, book_price,
    book_cases, approved, users_count, cases_count, min_price, max_price,
    median_user_price, last_used}]} (prices.differences): where people's
    own prices are more than 10% off the book's. Never who."""
    db = SessionLocal()
    try:
        return {"groups": prices.differences(db)}
    finally:
        db.close()


class Decision(BaseModel):
    make: str = Field(min_length=1, max_length=80)
    model_key: str = Field(min_length=1, max_length=80)
    item_key: str = Field(min_length=1, max_length=200)
    decision: Literal["update", "keep"]
    unit_price: Optional[float] = Field(default=None, gt=0, le=prices.MAX_UNIT_PRICE)
    reason: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _price_for_update(self):
        if self.decision == "update" and self.unit_price is None:
            raise ValueError("unit_price is needed to update the book")
        return self


@router.post("/admin/price-differences/decide")
def decide_price(body: Decision, admin: CurrentUser = Depends(require_admin)) -> dict:
    """"Update book to $X" or "Keep book price" on one line of the list
    (prices.decide): a book_prices row and a history entry. 422 for an
    update without unit_price."""
    db = SessionLocal()
    try:
        row = prices.decide(db, admin, make=body.make, key=body.model_key, item=body.item_key,
                            decision=body.decision, unit_price=body.unit_price, reason=body.reason or "")
        was = f"{row.book_price_before:,.2f}" if row.book_price_before else "none"
        what = f"update to {row.unit_price:,.2f}" if row.decision == "update" else "keep"
        audit(db, admin, "book_price_decided",
              detail=f"{prices.label(row.make, row.model_key, row.wording)}: {what} (book was {was}; "
                     f"{row.users_count} people, {row.cases_count} cases)" + (f"; {row.reason}" if row.reason else ""))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

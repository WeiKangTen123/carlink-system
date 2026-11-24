"""Stage 1: "My prices", confirming prices, and the admins' "All users'
prices" (owner approved 24 Nov 2026; plans/2026-11-23-report-and-parts/
S1-contract.md and the owner's final price model of 24 Nov 2026; the
logic is app/parts/prices.py).

    GET    /me/prices                            this person's own prices (?make=&model=&q=)
    PATCH  /me/prices/{id}                       {unit_price} -> the row
    DELETE /me/prices/{id}                       a history entry with the old price (DB3)
    POST   /me/prices/{id}/undo                  the price before the last change back
    POST   /reports/{id}/annex-a/confirm-prices  the waiting Annex A prices confirmed
    GET    /admin/user-prices                    admin: everyone's own prices (?user=&make=&model=&q=)
    POST   /admin/user-prices/{id}/promote       admin: use that price for everyone

Prices are personal: "My prices" are every signed-in account's own (owner
P3), never anyone else's -- another person's row is 404, the same as one
that doesn't exist. Admins see everyone's, with the person's name (the
owner's wish), and may make one the shared default; nobody else sees
another person's prices. The old "Price differences" routes and their
"keep" decision are gone (the final model replaced them).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func

from app.api.annexes import _editable
from app.auth.core import CurrentUser, audit
from app.auth.deps import ApiError, current_user, editable_case, require_admin
from app.parts import prices
from app.parts.history import model_key, split_make_model
from app.reports.db import SessionLocal
from app.reports.models import User, UserPrice
from app.reports.synthesize_annexes import synthesize_annexes_for_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prices"])

NOT_FOUND = "No such price of yours."
NOT_LISTED = "No such price."


def _above_zero(value: float | None) -> float | None:
    """A price kept to the cent must still be above 0: 0.004 passes gt=0
    and is stored as 0.00 (review Low 2)."""
    if value is not None and round(float(value), 2) <= 0:
        raise ValueError("a price of at least 0.01")
    return value


class UnitPrice(BaseModel):
    unit_price: float = Field(gt=0, le=prices.MAX_UNIT_PRICE)

    @field_validator("unit_price")
    @classmethod
    def cents_above_zero(cls, value):
        return _above_zero(value)


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
    kept for Undo. Answers the row as GET /me/prices lists it. 422 for a
    price that is 0 to the cent."""
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
    """"Confirm all prices" (owner P5): every Annex A price that is waiting
    -- filled in from the person's own prices, an approved or a book price
    (prices.awaits_confirmation) -- confirmed (price_confirmed true); the
    person has checked them against the workshop's quotation. Nothing else
    is touched (review M2): a line from before Stage 1, or with no price
    to confirm, stays as it was. As every Annex A change: editable_case,
    409 for a draft or a signed-off case; the PDF is rendered again on the
    next download. A history entry says how many. Answers {annex_a,
    annex_c} as the Annex A save (PUT /reports/{id}/annex-a) does, and
    how many lines it confirmed (confirmed: exactly the lines changed)."""
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        data = dict(r.data or {})
        annex = dict(data.get("annex_a") or {})
        items, confirmed = [], 0
        for row in annex.get("items") or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            if prices.awaits_confirmation(row):
                row["price_confirmed"] = True
                confirmed += 1
            items.append(row)
        annex["items"] = items
        data["annex_a"] = annex
        # The totals as every Annex A save works them out (no figure moved).
        data = synthesize_annexes_for_report(data)
        r.data = data
        # As after every Annex A change (review Low 11): rendered again on
        # the next download.
        r.pdf_path = None
        audit(db, user, "annex_a_prices_confirmed", r.id,
              detail=f"{confirmed} price{'s' if confirmed != 1 else ''} confirmed ({len(items)} line(s))")
        db.commit()
        return {"annex_a": data["annex_a"], "annex_c": data["annex_c"], "confirmed": confirmed}
    finally:
        db.close()


# ---------------------------------------------------- All users' prices


@router.get("/admin/user-prices")
def all_user_prices(user: str = "", make: str = "", model: str = "", q: str = "",
                    admin: CurrentUser = Depends(require_admin)) -> dict:
    """{prices: [{id, user_id, user_name, make, model_key, item_key, part,
    wording, side, unit_price, default_price, default_source, updated_at,
    uses, last_case_id}]}, newest first (prices.user_price_rows): every
    person's own prices, with their name, and the shared default beside
    each (default_source "approved" | "book" | null). Admins only (403 for
    anyone else). user: an account id or words of a name or username;
    make, model and q as GET /me/prices takes them (q also finds the
    standard part name)."""
    db = SessionLocal()
    try:
        return {"prices": prices.user_price_rows(db, user=user, make=make, model=model, q=q)}
    finally:
        db.close()


class Promote(BaseModel):
    """The admin may change the price before using it for everyone; left
    out, it is the person's own."""
    unit_price: Optional[float] = Field(default=None, gt=0, le=prices.MAX_UNIT_PRICE)
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("unit_price")
    @classmethod
    def cents_above_zero(cls, value):
        return _above_zero(value)


def _money(value: float | None) -> str:
    return f"{value:,.2f}" if value else "none"


@router.post("/admin/user-prices/{price_id}/promote")
def promote_user_price(price_id: int, body: Promote, admin: CurrentUser = Depends(require_admin)) -> dict:
    """"Use this price for everyone": the shared default for that make,
    model and line becomes this person's price (or the admin's figure,
    unit_price) -- a book_prices row, decision "update", active; older rows
    on the key inactive (prices.promote) -- with a history entry saying
    who did it (and when), the default before and after, and whose price
    it was. The person's own price is untouched. Answers the row as GET
    /admin/user-prices lists it, its default now the promoted one. 404 for
    a price that doesn't exist; 403 for anyone but an admin."""
    db = SessionLocal()
    try:
        row = db.get(UserPrice, price_id)
        if row is None:
            raise ApiError(404, NOT_LISTED, "price_not_found")
        made, before, source = prices.promote(db, admin, row, body.unit_price, body.reason or "")
        owner = db.get(User, row.user_id)
        whose = prices.user_name(owner) or "a removed account"
        was = f"{_money(before)} ({source})" if before else "none"
        audit(db, admin, "book_price_promoted",
              detail=f"{prices.label(row.make, row.model_key, row.wording)}: default {was} -> {made.unit_price:,.2f}, "
                     f"from {whose}'s price {row.unit_price:,.2f}" + (f"; {made.reason}" if made.reason else ""))
        db.commit()
        names = {row.user_id: prices.user_name(owner)} if owner is not None else {}
        return prices.user_price_row(row, prices.Defaults(db), names)
    finally:
        db.close()

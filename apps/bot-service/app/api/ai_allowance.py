"""What is left of today's free Gemini allowance on the keys the signed-in
person's AI uses.

    GET /auth/me/ai-allowance

Everyone is on free-tier keys: Google allows each Google Cloud project a
number of requests per model per day (20 on gemini-3-flash-preview) and
refills them at midnight US Pacific time. This says, per key and model,
how many the run log (models.AiRun attempts) shows used since that
midnight, the day's allowance when Google has stated it in a 429 (ai/
allowance.py learns it), and until when a model is used up.

    {"day_resets_at": "2026-11-10T08:00:00Z",
     "keys": [{"source": "own" | "company", "last4": "a91F" | null,
               "billed": bool | null,
               "models": [{"model": "...",
                           "role": "main" | "backup" | "sorting" | "second_look",
                           "used_today": 7, "limit": 20 | null,
                           "spent_until": ISO | null}]}]}

The keys are the ones a call for this person can use (ai/keys.resolve_keys):
their own key when they have saved one, then the company keys when they
may use them (auth/permissions.may_use_company_key). Never a key itself:
an own key's last 4 to its owner, a company key's only to admins (the
round-3 rule for key_last4), and the counts come from the run log, which
never holds more than the last 4 either.

used_today counts the tries that reached Google and were not refused:
a skipped try (day_spent) never left the server, and a 429 is Google
turning the request away, which its daily count doesn't include.
"""
from fastapi import APIRouter, Depends

from app.ai import allowance
from app.ai.keys import company_keys, get_row, last4
from app.auth.core import CurrentUser
from app.auth.deps import current_user
from app.auth.permissions import is_admin, may_use_company_key
from app.config import settings
from app.reports.db import SessionLocal
from app.reports.models import AiRun

router = APIRouter(tags=["ai-allowance"])

# Outcomes that don't count toward Google's daily count (the top of this file).
NOT_COUNTED = ("day_spent", "429")


def _chain(value) -> list[str]:
    """A model chain setting, "a,b,c" or a list; [] when unset."""
    if isinstance(value, str):
        return [m.strip() for m in value.split(",") if m.strip()]
    if isinstance(value, (list, tuple)):
        return [m.strip() for m in value if isinstance(m, str) and m.strip()]
    return []


def model_roles() -> list[tuple[str, str]]:
    """[(model, role)] in the order shown: the reading chain (its first
    model "main", the rest "backup"), then photo sorting's models, then the
    second look's, when that setting exists. A model in more than one keeps
    its first role."""
    roles: dict[str, str] = {}
    for i, model in enumerate(_chain(settings.gemini_model_chain)):
        roles.setdefault(model, "main" if i == 0 else "backup")
    for model in _chain(settings.gemini_triage_model_chain):
        roles.setdefault(model, "sorting")
    # Added by another change, maybe not here yet: read defensively.
    for model in _chain(getattr(settings, "gemini_second_look_chain", None)):
        roles.setdefault(model, "second_look")
    return list(roles.items())


def _used_today(db, *, user_id: str, own_last4: str | None, company_last4s: set[str]) -> dict:
    """{("own" | "company", last4): {model: tries}} since today's reset, from
    the run log. An own key's tries only from this person's runs: nobody
    else's calls use it, and someone else's own key can end in the same 4
    characters."""
    since = allowance.day_started().replace(tzinfo=None)  # naive UTC, as SQLite hands it back
    query = db.query(AiRun.actor_user_id, AiRun.attempts).filter(AiRun.started_at >= since)
    if not company_last4s:
        query = query.filter(AiRun.actor_user_id == user_id)
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for actor_id, attempts in query.all():
        for attempt in attempts or []:
            if not isinstance(attempt, dict) or attempt.get("outcome") in NOT_COUNTED:
                continue
            source, tail, model = attempt.get("key_source"), attempt.get("key_last4"), attempt.get("model")
            if not isinstance(model, str) or not tail:
                continue
            own = source == "own" and actor_id == user_id and tail == own_last4
            if not own and not (source == "company" and tail in company_last4s):
                continue
            per_model = counts.setdefault((source, tail), {})
            per_model[model] = per_model.get(model, 0) + 1
    return counts


def _models(fingerprint: str, roles: list[tuple[str, str]], used: dict[str, int]) -> list[dict]:
    state = allowance.key_state(fingerprint, [model for model, _ in roles])
    return [
        {
            "model": model,
            "role": role,
            "used_today": used.get(model, 0),
            "limit": state[model]["limit"],
            "spent_until": allowance.iso(state[model]["spent_until"]) if state[model]["spent_until"] else None,
        }
        for model, role in roles
    ]


@router.get("/auth/me/ai-allowance")
def my_ai_allowance(user: CurrentUser = Depends(current_user)) -> dict:
    roles = model_roles()
    company = company_keys() if may_use_company_key(user) else []
    db = SessionLocal()
    try:
        row = get_row(db, user.id)
        billed_company = None
        if company:
            # Imported here, as ai/keys.py does: intake/public imports half the app.
            from app.intake.public import company_key_billed

            billed_company = company_key_billed(db)
        used = _used_today(
            db,
            user_id=user.id,
            own_last4=row.last4 if row is not None else None,
            company_last4s={last4(k.value) for k in company},
        )
        out = []
        if row is not None:
            # The row's fingerprint, as AiKey's: nothing is unsealed here.
            out.append({
                "source": "own",
                "last4": row.last4,
                "billed": bool(row.billing_confirmed),
                "models": _models(row.fingerprint, roles, used.get(("own", row.last4), {})),
            })
        for key in company:
            tail = last4(key.value)
            out.append({
                "source": "company",
                "last4": tail if is_admin(user) else None,
                "billed": billed_company,
                "models": _models(key.fingerprint, roles, used.get(("company", tail), {})),
            })
        return {"day_resets_at": allowance.iso(allowance.next_reset()), "keys": out}
    finally:
        db.close()

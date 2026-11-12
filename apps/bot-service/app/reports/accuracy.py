"""The AI's accuracy on real cases (contract 8 L, 2026-11-12).

The four Excel cases are only a practice test: a real case has no answer
sheet. What it has is the surveyor's signed-off report, and beside it the
AI's own draft (Report.ai_draft, kept apart from Report.data since
2026-11-04). compare() sets the two side by side and counts what the
surveyor kept, removed, added or changed before signing:

  damage   the damaged parts, keyed as Annex A keys them (prefill.item_key:
           the canonical part; a named "Other / Not Listed" part by its
           name). Kept: on both lists; removed by people: the AI's only;
           added by people (missed): the signed-off list's only. A part on
           both lists on another side (same family, history.family_and_side)
           is kept with side_changed; one the AI left "(side undetermined)"
           and a person placed is kept with side_set. On the kept parts:
           Repair / Replace (the item's remarks) and the Annex A condition
           (taxonomy.annex_condition) changed by people, or filled where the
           AI left them blank.
  annex_a  the lines the machine writes from the AI's damage list
           (machine_annex, from machine_annex_a: what filing writes, with the
           history as it was when the case was filed) against the
           signed-off lines, matched by wording and side (prefill.line_key),
           then by wording alone: kept, removed, added -- by where the added
           line came from -- and, on the kept lines the machine priced, the
           W/shop Est changed by people. Without machine_annex only the
           signed-off lines' own origins are counted.
  fields   the cover and parties fields the AI can fill (review.FIELD_RULES
           outside cover_page and Our Ref, which the AI never writes): filled
           by the AI and kept, changed or emptied; filled by people where the
           AI left them blank -- the firm's usual static-check wording, which
           filing puts in, counted apart as "template".

compare() is pure: no database, no AI. Old cases and missing keys count as
empty. The answer holds counts, canonical part names, Annex A wordings and
field keys -- never a value of the case (no plate, name or figure).
record() stores it at sign-off (api/main._after_sign_off) as an AiAccuracy
row; GET /admin/ai-accuracy (api/ai_accuracy.py) sums the rows.
"""
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.permissions import can_view_all_cases
from app.parts.history import family_and_side, normalise_item, restricted
from app.parts.learn import case_ref, signed_off_as
from app.parts.prefill import item_key, line_key, origin_of, sync_annex_a
from app.reports.models import AiAccuracy, AiRun, AuditEntry, Report, User
from app.reports.review import FIELD_RULES, TEMPLATE_DEFAULTS, is_blank, same_value, value_at
from app.reports.synthesize_annexes import synthesize_annexes_for_report
from app.reports.taxonomy import OTHER_PART, UNDETERMINED_SUFFIX, annex_condition, other_part_name, unsided_part

# Bump when a definition below changes, so rows made under the old ones can
# be told apart (and made again: scripts/backfill_ai_accuracy.py).
VERSION = 1

# The fields the AI can write: the cover page's own section is the
# surveyor's (schema.CoverPageInfo is left out of the AI's schema) and Our
# Ref never the model's (extraction._NOT_MODEL_OUTPUT).
AI_FIELDS = tuple(key for key in FIELD_RULES if key != "report_id" and not key.startswith("cover_page."))

# The runs whose model drafted the case: the reading and its re-reads. Not
# the sorter, nor the second look, whose ticked parts are a person's.
READ_KINDS = ("read", "redraft", "redetect")

# Name lists are kept short: enough for the most-missed lists, not a copy
# of the case.
MAX_NAMES = 60

DAMAGE_COUNTS = ("ai_found", "final", "kept", "removed", "added", "side_changed", "side_set",
                 "remarks_changed", "remarks_filled", "condition_changed", "condition_filled")
ANNEX_COUNTS = ("machine", "kept", "removed", "added", "added_machine", "added_suggested", "added_typed",
                "priced_kept", "price_changed", "price_filled")
FIELD_COUNTS = ("ai_filled", "kept", "changed", "emptied", "filled", "template")


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(value) -> str:
    """For comparing wordings: trimmed, one space, any letter case."""
    return " ".join(str(value or "").split()).lower()


# ------------------------------------------------------------- damage


def damage_items(data: dict) -> list[dict]:
    """The damage list: damage_summary, else the older damaged_parts (as
    prefill._damaged reads a case)."""
    items = data.get("damage_summary")
    items = [it for it in items if isinstance(it, dict) and str(it.get("part") or "").strip()] if isinstance(items, list) else []
    if not items:
        parts = data.get("damaged_parts")
        items = [{"part": p} for p in parts if isinstance(p, str) and p.strip()] if isinstance(parts, list) else []
    return items


def _parts(data: dict) -> dict[str, dict]:
    """key -> the first item of that part: two entries naming one part are
    one part (as Annex A has them)."""
    out: dict[str, dict] = {}
    for item in damage_items(data):
        key = item_key(item)
        if key and key not in out:
            out[key] = item
    return out


def part_label(key: str, item: dict) -> str:
    """How a part is named in the lists: its canonical name, or "Other /
    Not Listed: <name as written>"."""
    other = other_part_name(item)
    return f"{OTHER_PART}: {other}" if other else key


def _condition(item: dict) -> str:
    try:
        return annex_condition(item)
    except Exception:  # an old item's odd condition words: nothing to compare
        return ""


def _pair_parts(ai: dict, final: dict) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """([(AI key, signed-off key, "same" | "side_changed" | "side_set")],
    the AI's keys left, the signed-off keys left). The same key first; then
    the same family on another side; then a "(side undetermined)" name and
    a sided part it stands for ("Door (side undetermined)" is either front
    door, which family_and_side calls "Front Door")."""
    pairs = [(k, k, "same") for k in ai if k in final]
    left_ai = [k for k in ai if k not in final]
    left_final = [k for k in final if k not in ai]

    def take(test) -> str | None:
        found = next((f for f in left_final if test(f)), None)
        if found is not None:
            left_final.remove(found)
        return found

    for a in list(left_ai):
        family, side = family_and_side(a)
        f = take(lambda f: family_and_side(f)[0] == family)
        if f is None and a.endswith(UNDETERMINED_SUFFIX):
            f = take(lambda f: unsided_part(f) == a)
        elif f is None:
            f = take(lambda f: f.endswith(UNDETERMINED_SUFFIX) and unsided_part(a) == f)
        if f is None:
            continue
        left_ai.remove(a)
        f_side = family_and_side(f)[1]
        if side is None and f_side is None:
            how = "same"  # one part by two names ("Quarter Panel", "Rear Quarter Panel")
        elif side is None:
            how = "side_set"
        else:
            how = "side_changed"  # the other side, or a person couldn't say which
        pairs.append((a, f, how))
    return pairs, left_ai, left_final


def _damage(ai_draft: dict, final_data: dict) -> dict:
    ai, final = _parts(ai_draft), _parts(final_data)
    pairs, removed, added = _pair_parts(ai, final)
    out = {key: 0 for key in DAMAGE_COUNTS}
    out.update(ai_found=len(ai), final=len(final), kept=len(pairs), removed=len(removed), added=len(added))
    side_changed, remarks_changed = [], []
    for a, f, how in pairs:
        if how != "same":
            out[how] += 1
        if how == "side_changed":
            side_changed.append([part_label(a, ai[a]), part_label(f, final[f])])
        for name, value_of in (("remarks", lambda it: it.get("remarks")), ("condition", _condition)):
            before, after = _text(value_of(ai[a])), _text(value_of(final[f]))
            if before and after and before != after:
                out[f"{name}_changed"] += 1
                if name == "remarks":
                    remarks_changed.append(part_label(f, final[f]))
            elif not before and after:
                out[f"{name}_filled"] += 1
    out["removed_parts"] = [part_label(k, ai[k]) for k in removed][:MAX_NAMES]
    out["added_parts"] = [part_label(k, final[k]) for k in added][:MAX_NAMES]
    out["side_changed_parts"] = side_changed[:MAX_NAMES]
    out["remarks_changed_parts"] = remarks_changed[:MAX_NAMES]

    # Whether the AI's own confidence, and (zone reading, contract 8 D2)
    # which reads found a part, say anything about it being kept.
    kept_keys = {a for a, _, _ in pairs}
    by_confidence: dict[str, dict] = {}
    by_read: dict[str, dict] = {}
    for key, item in ai.items():
        outcome = "kept" if key in kept_keys else "removed"
        confidence = str(item.get("ai_confidence") or "").strip().capitalize()
        confidence = confidence if confidence in ("High", "Medium", "Low") else "Unrated"
        by_confidence.setdefault(confidence, {"kept": 0, "removed": 0})[outcome] += 1
        reads = item.get("found_by")
        for read in reads if isinstance(reads, list) else []:
            by_read.setdefault(str(read)[:40], {"kept": 0, "removed": 0})[outcome] += 1
    out["by_confidence"] = by_confidence
    if by_read:
        out["by_read"] = by_read
    return out


# ------------------------------------------------------------ Annex A


def _rows(data: dict) -> list[dict]:
    annex = data.get("annex_a")
    items = annex.get("items") if isinstance(annex, dict) else None
    return [r for r in items or [] if isinstance(r, dict) and str(r.get("part_name") or "").strip()]


def machine_made(row: dict, damaged_parts=()) -> bool:
    """A line the machine wrote -- from the damage list, past cases, a
    looked-up or catalogue price -- whatever a person did to it since: made
    for a damaged part (source_part, which only the machine sets), or an
    origin other than "typed" (prefill.origin_of, for rows saved before
    origins existed). A line a person picked from the suggestions panel is
    theirs, as prefill._machine_owned reads it: it says where it came from
    (suggested_from, saved with origin "history") but was never prefilled
    nor made for a part."""
    if row.get("source_part"):
        return True
    if row.get("suggested_from") and not row.get("prefilled"):
        return False
    try:
        return origin_of(row, damaged_parts) != "typed"
    except Exception:
        return False


def _match_lines(machine: list[dict], final: list[dict]) -> list[tuple[int, int]]:
    """(machine index, signed-off index) pairs, each line used once: the
    same wording on the same side first (prefill.line_key), then the same
    wording on any side (a line worded without a side while the side was
    unconfirmed, and with it after)."""
    pairs: list[tuple[int, int]] = []
    free = list(range(len(final)))
    done: set[int] = set()
    for key_of in (lambda name: line_key(name), lambda name: normalise_item(name or "")):
        for i, row in enumerate(machine):
            if i in done:
                continue
            key = key_of(row.get("part_name") or "")
            j = next((j for j in free if key_of(final[j].get("part_name") or "") == key), None)
            if j is not None:
                pairs.append((i, j))
                free.remove(j)
                done.add(i)
    return pairs


def _annex(final_data: dict, machine_annex: list | None) -> dict:
    final = _rows(final_data)
    damaged = {str(it.get("part") or "").strip() for it in damage_items(final_data)}
    made = [machine_made(r, damaged) for r in final]
    out = {"final": len(final), "final_machine": sum(made), "final_typed": len(final) - sum(made)}
    if machine_annex is None:
        out.update({key: None for key in ANNEX_COUNTS})
        out.update(removed_lines=[], added_lines=[])
        return out
    machine = [r for r in machine_annex if isinstance(r, dict) and str(r.get("part_name") or "").strip()]
    pairs = _match_lines(machine, final)
    priced_kept = price_changed = price_filled = 0
    for i, j in pairs:
        before, after = round(_num(machine[i].get("workshop_est")), 2), round(_num(final[j].get("workshop_est")), 2)
        if before > 0:
            priced_kept += 1
            if abs(before - after) > 0.005:
                price_changed += 1
        elif after > 0:
            price_filled += 1
    matched_machine = {i for i, _ in pairs}
    matched_final = {j for _, j in pairs}
    added = [j for j in range(len(final)) if j not in matched_final]
    # The machine's lines for a part a person added (or a line they
    # reworded); lines picked from the suggestions panel; lines typed.
    added_machine = [j for j in added if made[j]]
    added_suggested = [j for j in added if not made[j] and final[j].get("suggested_from")]
    added_typed = [j for j in added if not made[j] and not final[j].get("suggested_from")]
    out.update(
        machine=len(machine), kept=len(pairs), removed=len(machine) - len(pairs), added=len(added),
        added_machine=len(added_machine), added_suggested=len(added_suggested), added_typed=len(added_typed),
        priced_kept=priced_kept, price_changed=price_changed, price_filled=price_filled,
    )
    out["removed_lines"] = [str(machine[i]["part_name"]).strip()[:80] for i in range(len(machine)) if i not in matched_machine][:MAX_NAMES]
    # The lines people add that the machine never writes (the parts they
    # added are counted under damage).
    out["added_lines"] = [str(final[j]["part_name"]).strip()[:80] for j in added_suggested + added_typed][:MAX_NAMES]
    return out


# ------------------------------------------------------------- fields


def _fields(ai_draft: dict, final_data: dict) -> dict:
    out: dict = {key: 0 for key in FIELD_COUNTS}
    by_key: dict[str, str] = {}
    for key in AI_FIELDS:
        before, after = value_at(ai_draft, key), value_at(final_data, key)
        if not is_blank(before):
            out["ai_filled"] += 1
            outcome = "kept" if same_value(before, after) else "emptied" if is_blank(after) else "changed"
        elif is_blank(after):
            continue
        elif key in TEMPLATE_DEFAULTS and same_value(after, TEMPLATE_DEFAULTS[key]):
            outcome = "template"
        else:
            outcome = "filled"
        out[outcome] += 1
        by_key[key] = outcome
    out["by_key"] = by_key
    return out


def compare(ai_draft: dict | None, final_data: dict | None, machine_annex: list | None = None) -> dict:
    """The AI's draft against the case as signed off (see the module's
    docstring). machine_annex: the Annex A rows the machine writes from the
    AI's damage list (machine_annex_a); None leaves the Annex A comparison
    out and counts only the signed-off lines' origins."""
    ai = ai_draft if isinstance(ai_draft, dict) else {}
    final = final_data if isinstance(final_data, dict) else {}
    return {
        "version": VERSION,
        "damage": _damage(ai, final),
        "annex_a": _annex(final, machine_annex),
        "fields": _fields(ai, final),
    }


# ---------------------------------------------------------- recording


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def filed_at(db: Session, report: Report) -> datetime:
    """When the case was filed: the wizard's "finalized" history entry (its
    row was made when the draft was started), else when the row was made
    (the bots make it at filing). Naive UTC."""
    at = (
        db.query(func.min(AuditEntry.created_at))
        .filter(AuditEntry.report_id == report.id, AuditEntry.action == "finalized")
        .scalar()
    )
    return _naive_utc(at or report.created_at or datetime.now(timezone.utc))


def machine_annex_a(db: Session, report: Report) -> list[dict]:
    """The Annex A the machine writes from the AI's own damage list, as
    filing does (filing.file_case_data: the annexes rebuilt, then
    prefill.sync_annex_a), from the past-case history as it was when the
    case was filed -- not lines learned or imported since, which would make
    a price the surveyor confirmed look changed -- and never the case's
    own lines. The vehicle is the signed-off one: make and model are
    measured with the fields; this measures the damage list and the prices.
    Priced only when the case's owner may see the price book (filing.
    price_book_for)."""
    ai = report.ai_draft if isinstance(report.ai_draft, dict) else {}
    data = report.data if isinstance(report.data, dict) else {}
    items = [dict(it) for it in damage_items(ai)]
    vehicle = data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {}
    base = {
        "vehicle_info": dict(vehicle),
        "damage_summary": items,
        "damaged_parts": [it["part"] for it in items],
        "annex_a": {},
        "annex_b": {},
    }
    owner = db.get(User, report.owner_id) if report.owner_id else None
    price_book = owner is None or can_view_all_cases(owner)
    with restricted(exclude_refs={case_ref(report.id)}, imported_before=filed_at(db, report)):
        out = synthesize_annexes_for_report(base)
        out = sync_annex_a(db, out, price_book=price_book)
    return [dict(r) for r in ((out.get("annex_a") or {}).get("items") or [])]


def reading_models(db: Session, report_id: str) -> list[str]:
    """The models whose reading drafted the case, from the run log (ai/
    runs.py), first used first."""
    rows = (
        db.query(AiRun.model_used)
        .filter(AiRun.report_id == report_id, AiRun.kind.in_(READ_KINDS), AiRun.status == "ok")
        .order_by(AiRun.started_at, AiRun.id)
        .all()
    )
    out: list[str] = []
    for (model,) in rows:
        if model and model not in out:
            out.append(model)
    return out


def _signature(report: Report) -> str | None:
    sign_off = (report.data or {}).get("sign_off") if isinstance(report.data, dict) else None
    return sign_off.get("signature_date") if isinstance(sign_off, dict) else None


def signed_off_at(report: Report) -> datetime:
    """When it was signed off (sign_off.signature_date), naive UTC."""
    try:
        return _naive_utc(datetime.fromisoformat(str(_signature(report))))
    except ValueError:
        return _naive_utc(datetime.now(timezone.utc))


def measure(db: Session, report: Report) -> dict | None:
    """compare() for a stored case, with the machine's Annex A; None when
    the case has no AI draft to measure (filed before drafts were kept, or
    a draft the AI never read)."""
    if not isinstance(report.ai_draft, dict) or not report.ai_draft:
        return None
    return compare(report.ai_draft, report.data, machine_annex_a(db, report))


def record(db: Session, report_id: str, signature_date: str | None = None) -> dict | None:
    """Works the case's accuracy out and stores it, replacing the row of an
    earlier sign-off; commits. Returns the metrics, or None when nothing
    was stored: no AI draft, or the case is no longer signed off by this
    sign-off (signature_date; None: whichever it is now -- the backfill).

    The work is done first, outside any write: rebuilding the machine's
    Annex A reads the whole history and must not hold SQLite's write lock.
    Then the old row is deleted -- the transaction's first write, which
    takes the lock -- and the case is read again: a reopen or another
    sign-off committed meanwhile wins, and nothing is written."""
    report = db.get(Report, report_id)
    if not signed_off_as(report, signature_date):
        return None
    pinned = _signature(report)
    metrics = measure(db, report)
    models = reading_models(db, report_id)
    db.rollback()
    db.query(AiAccuracy).filter(AiAccuracy.report_id == report_id).delete(synchronize_session=False)
    db.expire_all()
    report = db.get(Report, report_id)
    if not signed_off_as(report, pinned):
        db.rollback()
        return None
    if metrics is not None:
        db.add(AiAccuracy(
            report_id=report_id,
            signed_off_at=signed_off_at(report),
            models=models,
            metrics=metrics,
            version=VERSION,
        ))
    db.commit()
    return metrics


def forget(db: Session, report_id: str) -> int:
    """Drops the case's row (reopened or deleted). The caller commits."""
    return db.query(AiAccuracy).filter(AiAccuracy.report_id == report_id).delete(synchronize_session=False)

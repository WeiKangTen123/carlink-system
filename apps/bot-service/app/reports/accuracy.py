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
           filing puts in, counted apart as "template". A case READ with
           INTAKE_USER_FIELDS on (app/reports/user_fields.py; its knowledge
           stamp's prompt says "+user-fields") was never asked for the
           workshop and the odometer reading -- the user's to type in, not
           the AI's to fill: they are left out (ai_fields), and the row says
           so ("left_to_people": their keys), so rows from before and after
           the switch can be told apart. A case with no stamp is judged by
           the switch as it is now. Off: as before.
  suggestions  (contract 9, VERSION 2) the parts the AI suggested but did
           not list (data.second_look, kept since filing): shown, and of
           those added (on the signed-off list), dismissed (marked Not
           damaged) or left undecided -- in all, by where the suggestion
           came from, by the read that found it and by the re-check's
           verdict; and how many added ones were looked at first. Counts
           only: never a part's reason, photo or box. A part added from a
           suggestion still counts under damage as added by people.
           VERSION 3 (contract 10): source "link" too, and -- on a case
           that had linked parts -- the same counts by link type, bucket,
           hit level and table rule (link_rule), by the linked check's
           verdict (link_check), the table version (links_table), and in
           shadow mode what the linked parts kept on the AI draft
           (ai_draft["link_suggestions"]) predicted against the signed-off
           list (shadow). A case without any has none of these keys.
           Still VERSION 3, added only on a case whose linked parts carry
           them (the car map, K4/K3b; a case without has none of these
           keys, so older rows and the sums read as before): by the car
           map's group (by_assembly, its id without the side), by hop
           (by_hop, "1" / "2"), the parts in the folded strip-down block
           (folded) and those a yes sign of the assembly walk lifted or
           opened (sign_lifted); and with intake_car_map "shadow" what the
           car map's linked parts on the AI draft
           (ai_draft["car_map_suggestions"]) predicted (car_map_shadow).
  regroups  (R-batching B5, still VERSION 3, only on a case whose photo
           index keeps the sorter's own kind of a photo a person regrouped,
           photo_index[i].ai_kind -- settings.intake_keep_sort_label; a case
           without has no such key, so older rows and the sums read as
           before): the photos with a kind (photos), those a person moved
           to another kind (moved), and each move as "sorter>person"
           (pairs). A photo the rescue sort (B9) had put back keeps
           "rescue:<kind>" as its first kind: its moves are pairs
           "rescue:damage>interior" and rescue_moved (only when there are
           some), never the sorter's moved.

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
from app.reports.suggestions import key_of, standing
from app.reports.synthesize_annexes import synthesize_annexes_for_report
from app.reports.taxonomy import OTHER_PART, UNDETERMINED_SUFFIX, annex_condition, other_part_name, unsided_part

# Bump when a definition below changes, so rows made under the old ones can
# be told apart (and made again: scripts/backfill_ai_accuracy.py). 2: the
# suggestions section (contract 9). 3: its linked parts (contract 10).
VERSION = 3

# The fields the AI can write: the cover page's own section is the
# surveyor's (schema.CoverPageInfo is left out of the AI's schema) and Our
# Ref never the model's (extraction._NOT_MODEL_OUTPUT).
AI_FIELDS = tuple(key for key in FIELD_RULES if key != "report_id" and not key.startswith("cover_page."))


def ai_fields(final_data: dict | None = None) -> tuple[str, ...]:
    """The fields the AI could fill on this case: AI_FIELDS without the
    workshop and the odometer reading when it was read with
    INTAKE_USER_FIELDS on -- the user's to type in, never the AI's to fill
    (app/reports/user_fields.py). Judged by the case's own read stamp
    (final_data["knowledge"]["prompt"] holds "+user-fields": knowledge.
    read_prompt), so a case read with the switch off and signed off after
    it went on keeps them, and the other way round; with no stamp (a case
    from before the stamp, or none given), by the switch as it is now."""
    from app.reports import user_fields

    stamp = ((final_data or {}).get("knowledge") or {}) if isinstance(final_data, dict) else {}
    prompt = stamp.get("prompt") if isinstance(stamp, dict) else None
    read_on = f"+{user_fields.TAG}" in prompt if isinstance(prompt, str) else user_fields.on()
    if not read_on:
        return AI_FIELDS
    return tuple(key for key in AI_FIELDS if key not in user_fields.REVIEW_KEYS)

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
SUGGESTION_COUNTS = ("shown", "added", "dismissed", "left")
SUGGESTION_GROUPS = ("by_source", "by_read", "by_check")
# Where a suggestion came from (SecondLookItem.source) and the re-check's
# verdicts (SuggestionCheck.verdict); "none": not re-checked. "other_side":
# F3's "Other side?" suggestions (settings.intake_keep_other_side), counted
# only on a case that has them.
SUGGESTION_SOURCES = ("zone", "kit", "nearby", "link", "other_side")
CHECK_VERDICTS = ("yes", "no", "unsure")
# Contract 10: a linked part's groups -- (group, its field, the values
# counted; None: any, cut to MAX_RULE) -- and the linked check's verdicts.
LINK_GROUPS = (
    ("by_link_type", "link_type", ("attached", "opens_with", "behind", "crush")),
    ("by_bucket", "bucket", ("seen", "likely", "strip_down")),
    ("by_hit", "hit", ("light", "medium", "heavy", "crush")),
    ("by_rule", "link_rule", None),
)
LINK_CHECK_VERDICTS = ("seen", "likely", "strip_down", "no", "cannot_tell")
# A link_rule as counted ("L1:rear-quarter-panel>rear-wheel-arch-liner" is
# 43 characters; two rules must not run into one).
MAX_RULE = 60
# The car map's (K4, K3b; still VERSION 3, each key only on a case that has
# it): groups by the car map's assembly and by hop, and tallies of the
# folded strip-down block and of the parts a yes sign lifted or opened.
CARMAP_GROUPS = ("by_assembly", "by_hop")
CARMAP_TALLIES = ("folded", "sign_lifted")


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
    keys = ai_fields(final_data)
    for key in keys:
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
    if keys != AI_FIELDS:
        out["left_to_people"] = [key for key in AI_FIELDS if key not in keys]
    return out


# -------------------------------------------------------- suggestions


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _suggestion_counts() -> dict:
    return {key: 0 for key in SUGGESTION_COUNTS}


def _suggestions(ai_draft: dict, final_data: dict) -> dict:
    """What became of the AI's suggestions (data.second_look, server-owned
    since contract 9). Shown: the keys of its items, of the decisions on
    them and of the dismissed list, each once -- a decided one is in items
    too (second_look._store keeps it), and one an old draft's re-read
    dropped from them is still one a person saw. Each is "added" when the
    key is on the signed-off damage list (whoever put it there), else
    "dismissed" when it is in dismissed, else "left". What a suggestion
    was -- its source, the reads that found it, the re-check's verdict --
    from the case's items, else the AI's frozen zone suggestions
    (Report.ai_draft, filing), else the item added from it."""
    look = final_data.get("second_look") if isinstance(final_data.get("second_look"), dict) else {}
    items = [it for it in _dicts(look.get("items")) if key_of(it)]
    decisions = [d for d in _dicts(look.get("decisions")) if d.get("key")]
    dismissed = {str(k) for k in look.get("dismissed") or [] if isinstance(k, str) and k}
    final_keys = {item_key(it) for it in damage_items(final_data)}
    added_items = {item_key(it): it for it in damage_items(final_data) if it.get("added_from") == "suggestion"}
    facts: dict[str, dict] = {}
    for it in items:
        facts.setdefault(key_of(it), it)
    for it in _dicts(ai_draft.get("zone_suggestions")):
        if key_of(it):
            facts.setdefault(key_of(it), {**it, "source": it.get("source") or "zone"})
    shown: list[str] = []
    for key in [key_of(it) for it in items] + [str(d["key"]) for d in decisions if d.get("action") != "undone"] + sorted(dismissed):
        if key not in shown:
            shown.append(key)
    still = standing(decisions)

    out: dict = {**_suggestion_counts(), "looked_first": 0, **{group: {} for group in SUGGESTION_GROUPS}}
    tables: list[str] = []
    for key in shown:
        outcome = "added" if key in final_keys else "dismissed" if key in dismissed else "left"
        fact = facts.get(key) or added_items.get(key) or {}
        source = fact.get("source") if fact.get("source") in SUGGESTION_SOURCES else None
        reads = [str(r)[:40] for r in fact.get("found_by") or [] if isinstance(r, str) and r]
        check = fact.get("check") if isinstance(fact.get("check"), dict) else {}
        verdict = check.get("verdict") if check.get("verdict") in CHECK_VERDICTS else "none"
        tallies = [out, out["by_check"].setdefault(verdict, _suggestion_counts())]
        if source:
            tallies.append(out["by_source"].setdefault(source, _suggestion_counts()))
        tallies += [out["by_read"].setdefault(read, _suggestion_counts()) for read in dict.fromkeys(reads)]
        if source == "link":
            tallies += _link_tallies(out, fact, tables)
        for tally in tallies:
            tally["shown"] += 1
            tally[outcome] += 1
        if outcome == "added" and any(d.get("action") == "added" and d.get("looked") for d in still.get(key) or []):
            out["looked_first"] += 1
    shadow = _shadow(ai_draft, final_data, tables)
    if shadow is not None:
        _link_groups(out)["shadow"] = shadow
    # The car map's own linked parts in its shadow mode (K4): their table is
    # not the one shown, so its version stays out of links_table.
    car_shadow = _shadow(ai_draft, final_data, [], "car_map_suggestions")
    if car_shadow is not None:
        _link_groups(out)["car_map_shadow"] = car_shadow
    if "links_table" in out:
        out["links_table"] = tables[0] if tables else None
    return out


def _table_of(rule) -> str | None:
    """The table version a link_rule names ("L1" of "L1:rear-bumper>beam")."""
    return rule.split(":", 1)[0][:20] or None if isinstance(rule, str) and ":" in rule else None


def _link_groups(out: dict) -> dict:
    """In place: the contract 10 groups on a suggestions section, made when
    a case first has a linked part."""
    for group, _, _ in LINK_GROUPS:
        out.setdefault(group, {})
    out.setdefault("link_check", {})
    out.setdefault("links_table", None)
    return out


def _link_tallies(out: dict, fact: dict, tables: list[str]) -> list[dict]:
    """The link groups' tallies one linked part counts in: its type,
    bucket, hit, rule and the linked check's verdict -- names only."""
    _link_groups(out)
    tallies = []
    for group, field, values in LINK_GROUPS:
        value = fact.get(field)
        if not isinstance(value, str) or not value or (values is not None and value not in values):
            continue
        tallies.append(out[group].setdefault(value[:MAX_RULE], _suggestion_counts()))
    table = _table_of(fact.get("link_rule"))
    if table and table not in tables:
        tables.append(table)
    found = fact.get("link_check") if isinstance(fact.get("link_check"), dict) else {}
    if found.get("verdict") in LINK_CHECK_VERDICTS:
        tallies.append(out["link_check"].setdefault(found["verdict"], _suggestion_counts()))
    return tallies + _carmap_tallies(out, fact)


def _carmap_tallies(out: dict, fact: dict) -> list[dict]:
    """The car map's tallies one linked part counts in (each group or
    tally made on first use): its assembly (the side cut off: one row for
    both front doors), its hop, the folded block, a yes sign."""
    tallies = []
    assembly = fact.get("assembly")
    if isinstance(assembly, str) and assembly.strip():
        name = assembly.split(".", 1)[0][:40]
        tallies.append(out.setdefault("by_assembly", {}).setdefault(name, _suggestion_counts()))
    hop = fact.get("hop")
    if hop in (1, 2) and not isinstance(hop, bool):
        tallies.append(out.setdefault("by_hop", {}).setdefault(str(hop), _suggestion_counts()))
    if fact.get("folded") is True:
        tallies.append(out.setdefault("folded", _suggestion_counts()))
    if isinstance(fact.get("signs_yes"), list) and fact["signs_yes"]:
        tallies.append(out.setdefault("sign_lifted", _suggestion_counts()))
    return tallies


def _shadow(ai_draft: dict, final_data: dict, tables: list[str], list_key: str = "link_suggestions") -> dict | None:
    """Shadow mode's outcome (contract 10): the linked parts the AI draft
    kept but nobody saw (ai_draft["link_suggestions"], or list_key: the
    car map's "car_map_suggestions"), one per key, and how many of them
    are on the signed-off list (by name, or by a name that stands for
    them: "Tail Lamp (side undetermined)" for a "Left Tail Lamp") -- in
    all and by rule. None when the draft has no such list."""
    if not isinstance(ai_draft.get(list_key), list):
        return None
    final = damage_items(final_data)
    final_keys = {item_key(it) for it in final}
    final_parts = {str(it.get("part")) for it in final}
    out: dict = {"predicted": 0, "on_final_list": 0, "by_rule": {}}
    seen: set[str] = set()
    for it in _dicts(ai_draft.get(list_key)):
        key = key_of(it)
        if not key or key in seen:
            continue
        seen.add(key)
        covers = [c for c in it.get("covers") or [] if isinstance(c, str)] or [str(it.get("part") or "")]
        on_list = key in final_keys or bool(set(covers) & final_parts)
        rule = it.get("link_rule") if isinstance(it.get("link_rule"), str) and it.get("link_rule") else None
        tallies = [out] + ([out["by_rule"].setdefault(rule[:MAX_RULE], {"predicted": 0, "on_final_list": 0})]
                           if rule else [])
        for tally in tallies:
            tally["predicted"] += 1
            tally["on_final_list"] += int(on_list)
        table = _table_of(rule)
        if table and table not in tables:
            tables.append(table)
    return out


def sum_suggestions(metrics: list[dict]) -> dict:
    """The suggestions sections of many cases' metrics added up (GET
    /admin/ai-accuracy); a row from before VERSION 2 has none and adds 0.
    links_table: the link table versions behind these counts, a sorted
    list (["L1"]) -- the key the AI-accuracy page reads, as one case's
    (a version or None); a row from before VERSION 3 adds none."""
    out: dict = {**_suggestion_counts(), "looked_first": 0, **{group: {} for group in SUGGESTION_GROUPS}}

    def add(into: dict, counts) -> None:
        for key in SUGGESTION_COUNTS:
            try:
                into[key] += int((counts or {}).get(key) or 0)
            except (TypeError, ValueError, AttributeError):
                pass

    _link_groups(out)
    tables: set[str] = set()
    out["shadow"] = {"predicted": 0, "on_final_list": 0, "by_rule": {}}

    def add_shadow(into: dict, counts) -> None:
        for key in ("predicted", "on_final_list"):
            try:
                into[key] += int((counts or {}).get(key) or 0)
            except (TypeError, ValueError, AttributeError):
                pass

    for m in metrics:
        s = m.get("suggestions") if isinstance(m, dict) and isinstance(m.get("suggestions"), dict) else None
        if s is None:
            continue
        add(out, s)
        try:
            out["looked_first"] += int(s.get("looked_first") or 0)
        except (TypeError, ValueError):
            pass
        for group in SUGGESTION_GROUPS:
            for name, counts in (s.get(group) or {}).items() if isinstance(s.get(group), dict) else ():
                if isinstance(counts, dict):
                    add(out[group].setdefault(str(name)[:40], _suggestion_counts()), counts)
        # Contract 10 (VERSION 3); a row from before has none of these.
        for group in [g for g, _, _ in LINK_GROUPS] + ["link_check"]:
            for name, counts in (s.get(group) or {}).items() if isinstance(s.get(group), dict) else ():
                if isinstance(counts, dict):
                    add(out[group].setdefault(str(name)[:MAX_RULE], _suggestion_counts()), counts)
        # One case's is a version or None; a list is taken too.
        found = s.get("links_table")
        tables.update(name[:20] for name in (found if isinstance(found, list) else [found])
                      if isinstance(name, str) and name)
        for shadow_key in ("shadow", "car_map_shadow"):
            shadow = s.get(shadow_key) if isinstance(s.get(shadow_key), dict) else None
            if shadow is None:
                continue
            into = out.setdefault(shadow_key, {"predicted": 0, "on_final_list": 0, "by_rule": {}})
            add_shadow(into, shadow)
            rules = shadow.get("by_rule") if isinstance(shadow.get("by_rule"), dict) else {}
            for name, counts in rules.items():
                if isinstance(counts, dict):
                    add_shadow(into["by_rule"].setdefault(
                        str(name)[:MAX_RULE], {"predicted": 0, "on_final_list": 0}), counts)
        # The car map's (K4, K3b): in the sum only once a row has them.
        for group in CARMAP_GROUPS:
            for name, counts in (s.get(group) or {}).items() if isinstance(s.get(group), dict) else ():
                if isinstance(counts, dict):
                    add(out.setdefault(group, {}).setdefault(str(name)[:40], _suggestion_counts()), counts)
        for tally in CARMAP_TALLIES:
            if isinstance(s.get(tally), dict):
                add(out.setdefault(tally, _suggestion_counts()), s[tally])
    out["links_table"] = sorted(tables)
    return out


def compare(ai_draft: dict | None, final_data: dict | None, machine_annex: list | None = None) -> dict:
    """The AI's draft against the case as signed off (see the module's
    docstring). machine_annex: the Annex A rows the machine writes from the
    AI's damage list (machine_annex_a); None leaves the Annex A comparison
    out and counts only the signed-off lines' origins."""
    ai = ai_draft if isinstance(ai_draft, dict) else {}
    final = final_data if isinstance(final_data, dict) else {}
    regroups = _regroups(final)
    return {
        "version": VERSION,
        "damage": _damage(ai, final),
        "annex_a": _annex(final, machine_annex),
        "fields": _fields(ai, final),
        "suggestions": _suggestions(ai, final),
        **({"regroups": regroups} if regroups is not None else {}),
    }


def _regroups(final: dict) -> dict | None:
    """B5: what people did to the sorter's kinds (see the module's
    docstring), or None when the case's photo index keeps no sorter's kind
    (photo_index[i].ai_kind) -- the setting off, nobody regrouped, or a case
    filed before it."""
    index = _dicts(final.get("photo_index"))
    if not any(isinstance(entry.get("ai_kind"), str) and entry["ai_kind"] for entry in index):
        return None
    pairs: dict[str, int] = {}
    moved = rescue_moved = 0
    for entry in index:
        was, now = entry.get("ai_kind"), entry.get("kind")
        if not (isinstance(was, str) and was and isinstance(now, str) and now):
            continue
        if was.startswith("rescue:"):
            if was[len("rescue:"):] == now:
                continue
            rescue_moved += 1
        elif was == now:
            continue
        else:
            moved += 1
        pair = f"{was}>{now}"
        pairs[pair] = pairs.get(pair, 0) + 1
    photos = sum(1 for entry in index if entry.get("kind") or entry.get("ai_kind"))
    out = {"photos": photos, "moved": moved, "pairs": dict(sorted(pairs.items()))}
    if rescue_moved:
        out["rescue_moved"] = rescue_moved
    return out


def sum_regroups(metrics: list[dict]) -> dict | None:
    """B5: the regroups summed over cases, or None when no case has any."""
    rows = [m["regroups"] for m in metrics if isinstance(m, dict) and isinstance(m.get("regroups"), dict)]
    if not rows:
        return None
    pairs: dict[str, int] = {}
    for row in rows:
        for pair, n in (row.get("pairs") or {}).items():
            if isinstance(n, int) and not isinstance(n, bool):
                pairs[pair] = pairs.get(pair, 0) + n
    total = lambda key: sum(r.get(key) for r in rows if isinstance(r.get(key), int) and not isinstance(r.get(key), bool))  # noqa: E731
    out = {"cases": len(rows), "photos": total("photos"), "moved": total("moved"), "pairs": dict(sorted(pairs.items()))}
    if total("rescue_moved"):
        out["rescue_moved"] = total("rescue_moved")
    return out


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

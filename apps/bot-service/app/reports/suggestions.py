"""What people decide about the AI's suggestions (contract 9, sections 4.3-4.5).

A suggestion is a part the AI thinks may be damaged but did not put on the
damage list: one only a zone read found (option B, intake/zones.py) or one
the second look named (ai/second_look.py). They live in
data["second_look"]["items"] and are never a finding until a person adds
one. The server owns data["second_look"]: a whole-draft save never changes
it (api/intake.save_intake_data, PUT /reports/{id}), only the decide
endpoints do, which stamp who and when:

    POST /intake/{id}/suggestions    a draft (api/intake.py)
    POST /reports/{id}/suggestions   a filed case (api/main.py)

    body {"key", "action": "add" | "dismiss" | "undo", "looked"}

  add      the suggestion becomes a damage item (item_from_suggestion),
           human_verified, added_from "suggestion"; a part already listed
           is not added twice (item null);
  dismiss  its key goes into second_look.dismissed: never suggested again on
           this case, through re-reads and filing;
  undo     takes back the key's latest decision still standing: a dismissal
           leaves dismissed, an added item leaves the list (and never goes
           into data.dismissed_parts -- that is "a listed part removed", a
           different thing).

Each decision is a SuggestionDecision in second_look.decisions, and a
history entry (suggestion_added / _dismissed / _undone) naming the part
only. The suggestion itself stays in second_look.items: the panel hides the
listed and dismissed ones, and "Show decided" and undo keep their evidence.

Also here: the "@<photo id>" -> "P07" relabel at filing (relabel_item,
relabel_second_look) and the decisions still standing (standing), which the
AI-accuracy record reads at sign-off (reports/accuracy.py).

Pure: no database, no request; the endpoints hold the transaction.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.intake.photos import photo_label
from app.parts.prefill import item_key, item_source
from app.reports.schema import MAX_DECISIONS, DamageSummaryItem, SecondLook, SuggestionDecision
from app.reports.taxonomy import OTHER_PART, damage_type_from_tokens, firm_condition

ACTIONS = ("add", "dismiss", "undo")
# SuggestionDecision.action -> its history entry.
HISTORY = {"added": "suggestion_added", "dismissed": "suggestion_dismissed", "undone": "suggestion_undone"}
# A key is a part name (prefill.item_key); anything longer is no key of ours.
MAX_KEY = 200


class UnknownSuggestion(Exception):
    """No suggestion (or decision to take back) with that key on this case."""


def key_of(item: dict) -> str:
    """A suggestion's key: its own, or -- an item stored before keys -- its
    part, which is the key of a canonical part."""
    return str(item.get("key") or item.get("part") or "")


def _find(items: list, key: str) -> dict | None:
    return next((it for it in items if isinstance(it, dict) and key_of(it) == key), None)


def standing(decisions: list) -> dict[str, list[dict]]:
    """key -> its decisions still standing, oldest first: each "undone"
    takes back the latest standing one of its key, so add, undo, dismiss
    leaves the dismissal."""
    out: dict[str, list[dict]] = {}
    for d in decisions or []:
        if not isinstance(d, dict) or not d.get("key"):
            continue
        mine = out.setdefault(str(d["key"]), [])
        if d.get("action") == "undone":
            if mine:
                mine.pop()
        elif d.get("action") in ("added", "dismissed"):
            mine.append(d)
    return out


def _listed(sug: dict, data: dict) -> bool:
    """Whether the suggestion's part is on the damage list already: a named
    "Other / Not Listed" by its name (two unlisted parts are two), any other
    part by its covers -- its own name and the names that stand for it, as
    the panel hides it (second_look.covers_of)."""
    from app.ai.second_look import covers_of, listed_parts

    items = [it for it in data.get("damage_summary") or [] if isinstance(it, dict)]
    if key_of(sug) in {item_key(it) for it in items}:
        return True
    part = str(sug.get("part") or "")
    if part == OTHER_PART:
        return False
    covers = [c for c in sug.get("covers") or [] if isinstance(c, str)] or covers_of(part)
    return bool(set(covers) & listed_parts(data))


def item_from_suggestion(sug: dict) -> dict:
    """The damage item a person adds from a suggestion: what the AI said
    about the part (its words, reason, evidence, the reads and photos that
    found it, the re-check's answer) on the photo of its best evidence, with
    that photo's box when the AI drew it -- so the table draws it as it
    drew the suggestion -- and the AI's confidence. Checked by the person
    who added it (human_verified), and marked as come from a suggestion
    (added_from).
    One builder for both pages (conflict #10)."""
    evidence = [e for e in sug.get("evidence") or [] if isinstance(e, dict) and e.get("photo")]
    photo = evidence[0]["photo"] if evidence else (sug.get("photo_reference") or None)
    # bbox_2d is the AI's box (the table and the evidence viewer label it
    # "AI box"): a sorter's box stays on its evidence entry, as "Sorter's
    # box", and the item gets none.
    box = (evidence[0].get("box") or None) if evidence and evidence[0].get("box_kind") == "ai" else None
    tokens = list(sug.get("condition_tokens") or [])
    condition = sug.get("condition") or None
    damage_type = sug.get("damage_type") or None
    # As drafting does (extraction._firm_condition) and POST /damage-items:
    # the condition is the firm's words, or the word for the damage type;
    # words with no type give the type they imply.
    if not condition:
        tokens, condition = firm_condition(tokens, damage_type)
    if not damage_type and tokens:
        damage_type = damage_type_from_tokens(tokens)
    return DamageSummaryItem(
        part=str(sug.get("part") or ""), other_part_name=sug.get("other_part_name"),
        damage_type=damage_type, condition_tokens=tokens, condition=condition,
        severity=sug.get("severity"), remarks=sug.get("remarks"),
        photo_reference=photo, bbox_2d=box, ai_confidence=sug.get("confidence"), human_verified=True,
        seen_in=list(sug.get("seen_in") or []), found_by=list(sug.get("found_by") or []),
        reason=sug.get("reason"), evidence=evidence, check=sug.get("check"), added_from="suggestion",
    ).model_dump()


@dataclass
class Decided:
    """What decide() did. data: the case data with second_look (and the
    damage list) as they are now. action: the decision recorded, None when
    nothing changed (a repeated click). item / item_index: the item added,
    or the one an undo took off the list, and its place there. listed:
    "added" | "removed" when the damage list changed (the case page then
    syncs Annex A), else None. part: the part's name for the history."""
    data: dict
    action: str | None = None
    item: dict | None = None
    item_index: int | None = None
    listed: str | None = None
    part: str = ""
    second_look: dict = field(default_factory=dict)

    def answer(self) -> dict:
        return {"second_look": self.second_look, "item": self.item, "item_index": self.item_index}


def _now() -> str:
    # As the field review stamps its marks (review._now).
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _with_list(data: dict, damage_summary: list[dict]) -> dict:
    return {**data, "damage_summary": damage_summary,
            "damaged_parts": [it.get("part") for it in damage_summary if it.get("part")]}


def decide(data: dict, key: str, action: str, looked: bool, user, where: str) -> Decided:
    """One person's add / dismiss / undo on the suggestion `key` (see the
    module's docstring), on a copy of `data`. Raises UnknownSuggestion when
    the case has no such suggestion -- nor, for an undo, a decision on it;
    ValueError for an action that isn't one of ACTIONS. A repeated click
    changes nothing (action None)."""
    from app.auth.core import display_name

    if action not in ACTIONS:
        raise ValueError(action)
    data = dict(data or {})
    look = SecondLook.model_validate(data.get("second_look") or {}).model_dump()
    sug = _find(look["items"], key)
    dismissed = list(look["dismissed"])
    damage_summary = [dict(it) for it in data.get("damage_summary") or [] if isinstance(it, dict)]
    # The decision an undo takes back. A decided suggestion stays in items
    # over a re-read (second_look._store), but an old draft's re-read
    # dropped it, so its part may come from that decision.
    latest = (standing(look["decisions"]).get(key) or [None])[-1] if action == "undo" else None
    part = str((sug or latest or {}).get("part") or key)
    done = Decided(data=data, second_look=look, part=item_source(sug) if sug else key)

    if action == "add":
        if sug is None and key not in {item_key(it) for it in damage_summary}:
            raise UnknownSuggestion(key)
        if sug is None or _listed(sug, data):
            return done  # listed already: nothing added twice
        item = item_from_suggestion(sug)
        damage_summary.append(item)
        done.item, done.item_index, done.listed = item, len(damage_summary) - 1, "added"
        done.action = "added"
    elif action == "dismiss":
        if key in dismissed:
            return done
        if sug is None:
            raise UnknownSuggestion(key)
        dismissed.append(key)
        done.action = "dismissed"
    else:
        if latest is not None and latest.get("action") == "added":
            # The item that add put on the list, if a person hasn't removed
            # or renamed it since; never into dismissed_parts.
            at = next((i for i in range(len(damage_summary) - 1, -1, -1)
                       if damage_summary[i].get("added_from") == "suggestion" and item_key(damage_summary[i]) == key), None)
            if at is not None:
                done.item, done.item_index, done.listed = damage_summary.pop(at), at, "removed"
        elif latest is not None or key in dismissed:
            # A dismissal -- also one from before decisions were recorded
            # (an old draft's dismissed list holds part names, the keys of
            # canonical parts).
            dismissed = [k for k in dismissed if k != key]
        elif sug is None:
            raise UnknownSuggestion(key)
        else:
            return done  # nothing to take back
        done.action = "undone"

    decision = SuggestionDecision(
        key=key, part=part, action=done.action, by_id=user.id, by_name=display_name(user) or None, at=_now(),
        looked=bool(looked), where=where,
    ).model_dump()
    look["dismissed"] = dismissed
    look["decisions"] = (list(look["decisions"]) + [decision])[-MAX_DECISIONS:]
    done.second_look = look
    done.data = {**(_with_list(data, damage_summary) if done.listed else data), "second_look": look}
    return done


# ------------------------------------------------------------ filing (4.3)


def final_label(ref, index_of: dict) -> str | None:
    """A draft's "@<photo id>" as the case's P-number, now the order is
    fixed; None for a photo deleted since. Any other reference as it is."""
    ref = str(ref or "")
    if not ref.startswith("@"):
        return ref or None
    index = index_of.get(ref[1:])
    return photo_label(index) if index is not None else None


def final_refs(refs, index_of: dict) -> list[str]:
    """A list of photo references (seen_in) as P-numbers, a photo deleted
    since dropped, each once."""
    out: list[str] = []
    for ref in refs or []:
        label = final_label(ref, index_of)
        if label and label not in out:
            out.append(label)
    return out


def relabel_item(item: dict, index_of: dict) -> dict:
    """A damage item or a suggestion with its photos as the case's
    P-numbers: photo_reference (a deleted photo's gone, and the box drawn
    on it), seen_in, and each evidence entry's photo (a deleted photo's
    entry dropped; a suggestion left with none is still kept)."""
    out = dict(item)
    if out.get("photo_reference"):
        out["photo_reference"] = final_label(out["photo_reference"], index_of)
        if out["photo_reference"] is None and "bbox_2d" in out:
            out["bbox_2d"] = None
    if out.get("seen_in"):
        out["seen_in"] = final_refs(out["seen_in"], index_of)
    if out.get("evidence"):
        evidence = []
        for entry in out["evidence"]:
            if not isinstance(entry, dict):
                continue
            photo = final_label(entry.get("photo"), index_of)
            if photo:
                evidence.append({**entry, "photo": photo})
        out["evidence"] = evidence
    return out


def relabel_second_look(look: dict | None, index_of: dict) -> dict | None:
    """data["second_look"] as filed: kept (the surveyor may still decide on
    the case page), its suggestions' photos as P-numbers. The decisions
    name no photo."""
    if not isinstance(look, dict):
        return look
    return {**look, "items": [relabel_item(it, index_of) for it in look.get("items") or [] if isinstance(it, dict)]}

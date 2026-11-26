"""Part observations (step 2B, K-pricing B.4 (1) CAPTURE; owner-approved 24
Nov 2026): one de-identified row per part decision on a signed-off case,
for the knowledge loop to learn from later (K3: counts per car type and
impact, with minimum evidence). Nothing here changes a case, a suggestion
or what the AI is asked; nothing reads the rows back into a case yet.

One row per part key (prefill.item_key: a canonical part, or a named
"Other / Not Listed" by its name):

  kept       on the AI's draft and on the signed-off list (paired as the AI
             accuracy record pairs them: the same part, the other side of
             its family, a "(side undetermined)" one a person placed --
             accuracy._pair_parts). The signed-off item's facts.
  removed    on the AI's draft only. The AI item's facts.
  added      on the signed-off list only. prompted when it was added
             because it was suggested (added_from "suggestion", or a
             standing "added" decision on its key): the echo guard (K3)
             counts these apart from what people added unprompted.
  dismissed  suggested (data.second_look), not on the list, marked Not
             damaged.
  left       suggested, not on the list, never decided.

How it got there (how): ai_read (the overall read found it; also every
part of a single-read case) | zone (only zone reads found it) |
suggestion (source zone / kit / nearby / other_side / sorter -- the photo
sorter's own, Detection Round 1) | link (link_rule) | catalogue (a link
from a parts-catalogue diagram, rule "<v>:oem>...") | hand_added.
found_by: the reads' names ("overall", "zone:rear"), or a photo sorter
suggestion's rule ("sorter_seen": only the sorter saw it; "sorter_intact":
the sorter saw it damaged where a zone read said intact).

Each row also carries the car (make, model key -- the price book's,
prices.price_key -- body type, a year band from the registration date),
where the car was hit (impact_end / impact_side from where the signed-off
parts are; impact_confirmed: a person confirmed the point of impact),
vehicle_confirmed (make and model a person's), the fittings renewed with
the part (its Annex A lines by source_part, the part's own line left
out: wording key, qty, list / nett, Necessary -- no price), and the
knowledge stamp of what made the case's AI draft, from its own AI runs
(knowledge.case_stamp, as data.knowledge).

NEVER: a plate, VIN, chassis or engine number, a name, a note or remark,
a photo or a price. An "Other" name and a fitting's wording are typed
text, so they are scrubbed (scrub_words; when in doubt a word goes): the
identifiers of the case and of every account (catalogue._secrets and the
rest, case_secrets), titles and the names after them ("per Mr Lim"),
any word with a digit, "@" or a letter outside a-z, the words of every
name on the case and of every account, and any other word of 3 letters
or fewer that is not a part word (plate prefixes, short names); then
normalised (history.normalise_item: "Rear air vent LH" -> "rear air
vent"). Condition words, hit and Repair / Replace are the firm's known
words only.

capture() writes them in the sign-off's background step (api/main.
_after_sign_off) beside the AI accuracy record, replacing the case's rows
of an earlier sign-off; forget() removes them on reopen and delete;
sweep_old() drops rows older than RETAIN (7 years, owner) and rows of a
case gone or no longer signed off (intake/cleanup.py runs it). The
backfill: scripts/backfill_part_observations.py. No AI call.
"""
import logging
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intake.links import end_of, hit_of
from app.parts.history import (
    canonical_part,
    family_and_side,
    family_of,
    model_key,
    normalise_item,
    side_of,
    split_make_model,
)
from app.parts.learn import signed_off_as
from app.parts.prefill import item_key, side_unconfirmed
from app.reports import knowledge
from app.reports.accuracy import (
    SUGGESTION_SOURCES,
    _pair_parts,
    _parts,
    _rows,
    _signature,
    signed_off_at,
)
from app.reports.models import PartObservation, Report
from app.reports.review import person_confirmed
from app.reports.suggestions import key_of, standing
from app.reports.taxonomy import (
    BODY_TYPES,
    FIRM_PART_NAMES,
    FIRM_PART_NAMES_HATCH,
    FIRM_REMARKS,
    FIRM_WHOLE_CONDITIONS,
    KNOWN_PARTS,
    OTHER_PART,
    body_type_for,
    condition_tokens_of,
    known_part,
    other_part_name,
    split_side,
)

logger = logging.getLogger(__name__)

# Bump when a definition below changes (the backfill makes the rows again).
# 2: the stricter scrubber, the stamp from the case's own AI runs, and a
# part's own Annex A line never one of its fittings (2B review M1, M2, L1).
VERSION = 2
OUTCOMES = ("kept", "removed", "added", "dismissed", "left")
HOWS = ("ai_read", "zone", "suggestion", "link", "catalogue", "hand_added")
AREAS = ("front", "rear", "side", "roof", "underbody", "cabin", "wheel")
IMPACT_ENDS = ("front", "rear", "both", "side", "other")
IMPACT_SIDES = ("left", "right", "both", "centre")
# The owner's retention rule (24 Nov 2026): at most 7 years.
RETAIN_YEARS = 7
MAX_FITTINGS = 20
MAX_READS = 6
MAX_WORDS = 6
MAX_NAME = 80
MAX_RULE = 60
# The id a parts-catalogue diagram's link gets (links._catalogue_links).
CATALOGUE_RULE = "oem>"

# A part at neither end of the car: its area by its name without the side.
# Every other such canonical part is on the side (doors, their glass,
# locks, hinges, handles and regulators, mirrors, sills).
_AREA_OF = {
    "Roof": "roof",
    "Underbody / Chassis": "underbody",
    "Airbag System": "cabin", "Dashboard": "cabin", "Interior Trim": "cabin", "Seat": "cabin",
    "Wheel": "wheel", "Tyre": "wheel", "Wheel Arch Liner": "wheel", "Wheel Arch Trim": "wheel",
    # A past line's family with no canonical part (history._FAMILY_RULES).
    "Wheel Nut": "wheel",
}
_BASES = frozenset(split_side(p)[0] for p in KNOWN_PARTS) - {OTHER_PART}

# Words of part names (the canonical and car map names and the firm's
# wording, sedan and hatch): a person's name word that is one of these
# ("Wing", "Lamp") is not taken out of a part name -- it would cut the part,
# not a name.
_PART_WORDS = (frozenset(
    w for text in list(KNOWN_PARTS) + list(FIRM_PART_NAMES.values()) + list(FIRM_PART_NAMES_HATCH.values())
    for w in re.findall(r"[a-z]+", text.lower())
) - {"be", "not", "named", "other", "listed"}) | {
    "lh", "rh", "left", "right", "front", "rear", "side", "assy", "set", "pcs", "clip", "clips", "bracket",
    "brackets", "cover", "garnish", "moulding", "retainer", "screw", "bolt", "nut", "seal", "emblem", "sticker",
    "vent", "air", "and", "with", "for", "the", "of",
}
# Words of 3 letters or fewer that may stay in a typed name: the short
# part words above and the trade's abbreviations. Every other short word
# goes (scrub_words): plate prefixes ("BKA", "SGX"), short names ("Ng",
# "Lim", "Tan"), titles ("Mr", "Dr") -- dropping a doubtful word is better
# than keeping a name.
_SHORT_OK = frozenset(w for w in _PART_WORDS if len(w) <= 3) | {
    "abs", "srs", "led", "hid", "drl", "ecu", "oem", "cv", "ac", "rr", "fr", "upr", "lwr", "otr", "inr", "ctr",
    "ext", "int", "std", "low", "mid", "hub", "oil", "rod", "pin", "box", "cup", "mat", "fin", "tie", "kit",
    "cap", "bag", "bar", "arm", "pc", "in", "on", "eye", "lip", "rib",
}
# A pillar's letter ("A pillar trim") stays before these.
_PILLAR = frozenset({"pillar", "pillars", "post"})
# A title before a name: it and the words after it go, up to the next part
# word ("per Mr Lim's quote", "Encik Azman", "Mdm Tan Siew Lan").
_TITLES = frozenset({
    "mr", "mrs", "ms", "miss", "mdm", "madam", "mister", "sir", "dr", "prof", "encik", "en", "cik", "puan", "pn",
    "tuan", "tn", "datuk", "dato", "datin", "sri", "haji", "hj", "hajjah", "hjh", "ir", "capt", "sgt",
    "insp", "cpl", "asp", "uncle", "auntie", "aunty",
})
# Between two parts of a name ("Azman bin Ali", "Kumar s/o Raj"): it, the
# word before it and the words after it go.
_NAME_LINKS = frozenset({"bin", "binti", "bte", "bt", "a/l", "a/p", "s/o", "d/o"})
_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
_TOKEN = re.compile(r"[^\s,;:()\[\]{}<>\"|]+")
_LETTERS = re.compile(r"[^\W\d_]+")


# ------------------------------------------------------------- privacy


def _key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _texts(data: dict, path: str) -> list:
    """The values at a dotted path, through lists ("people_involved.name")
    and, at a "*", through every value of a dict ("checked.*.by")."""
    nodes = [data]
    for part in path.split("."):
        nxt = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            values = list(node.values()) if part == "*" else [node.get(part)]
            for value in values:
                nxt.extend(value if isinstance(value, list) else [value])
        nodes = nxt
    return [n for n in nodes if isinstance(n, (str, int)) and not isinstance(n, bool)]


# Who is in a case: every field naming a person, a firm or a place a person
# can be found by -- their words are taken out of a typed name.
_NAME_FIELDS = (
    "reporter_name", "reporter_department", "company_name", "people_involved.name", "people_involved.department",
    "witnesses.name", "third_party_info.driver_name", "third_party_info.insurer_name", "vehicle_info.driver_name",
    "police_report.officer_name", "police_report.police_station", "insurance_details.adjuster_assigned",
    "insurance_details.workshop_assigned", "insurance_details.insurer_name", "cover_page.requested_by",
    "cover_page.requested_by_address", "cover_page.workshop_address", "sign_off.prepared_by",
    "sign_off.surveyor_name", "sign_off.reviewed_by", "sign_off.approved_by", "sign_off.signed_by_username",
    "sign_off.firm_name", "second_look.decisions.by_name", "checked.*.by", "location",
)
# And every contact or number that identifies someone, besides those
# catalogue._secrets already has (the case id, Our Ref, plate, VIN, engine
# and chassis no., the driver's, the policy and claim numbers).
_OTHER_SECRETS = (
    "reporter_contact", "reporter_email", "people_involved.contact", "witnesses.contact",
    "third_party_info.plate_number", "third_party_info.driver_nric", "third_party_info.driver_contact",
    "third_party_info.policy_number", "sign_off.signed_by_email", "sign_off.license_number",
    "police_report.report_number", "police_report.reference_number", "insurance_details.claim_number",
    "insurance_details.policy_number", "vehicle_info.plate_number", "vehicle_info.vin",
    "vehicle_info.engine_number", "vehicle_info.driver_contact",
)


def _name_words(values) -> set[str]:
    """Every word of 2+ letters of these names (any alphabet) that is not
    a part word; and every two words next to each other in one of them,
    as "kok wing": scrub_words drops such a pair whole, a part word in it
    too ("Kok Wing" the person, not a wing)."""
    words: set[str] = set()
    for value in values:
        found = _LETTERS.findall(str(value).lower())
        words.update(w for w in found if len(w) >= 2 and w not in _PART_WORDS)
        words.update(f"{a} {b}" for a, b in zip(found, found[1:], strict=False))
    return words


def case_secrets(report: Report, db: Session | None = None) -> tuple[list[str], set[str]]:
    """(identifiers, name words) of a case: what scrub_words takes out of a
    typed name. Identifiers: catalogue._secrets' and every other contact,
    number and name on the case (_NAME_FIELDS, _OTHER_SECRETS) and of
    every account (users: name, username, e-mail) -- letters and digits
    only, 5 or more. Name words: each word of 2+ letters of those names
    that is not a part word. `db` None: the accounts are left out (a case
    on its own, in tests)."""
    from app.parts.catalogue import _secrets

    data = report.data if isinstance(report.data, dict) else {}
    names = [v for path in _NAME_FIELDS for v in _texts(data, path)]
    others = [v for path in _OTHER_SECRETS for v in _texts(data, path)]
    if db is not None:
        from app.reports.models import User

        for name, username, email in db.query(User.name, User.username, User.email):
            names += [v for v in (name, username) if v]
            others += [str(email).split("@")[0]] if email else []
    secrets = list(_secrets(report))
    for value in names + others:
        key = _key(value)
        if len(key) >= 5 and key not in secrets:
            secrets.append(key)
    return secrets, _name_words(names)


def _letters(token: str) -> str:
    """A token's letters, lower case, without a possessive ("Lim's" ->
    "lim")."""
    token = re.sub(r"['’]s$", "", token, flags=re.I)
    return re.sub(r"[^a-z]", "", token.lower())


def _part_word(word: str) -> bool:
    return word in _PART_WORDS or word in _SHORT_OK


def scrub_words(text, secrets=(), name_words=()) -> str | None:
    """A typed part name or line wording made safe to keep, normalised;
    None when nothing is left. Taken out, in this order:
      - the case's identifiers however spaced (catalogue._scrubbed);
      - a title and the words after it up to the next part word ("per Mr
        Lim's quote"), and a name link with the word before it and the
        words after it ("Azman bin Ali");
      - any word with a digit or "@" (plates, VINs, phone, policy and claim
        numbers, e-mails), or a letter outside a-z ("Muñoz");
      - a word of a name on the case or of an account, and two words
        that stand together in one (name_words);
      - any other word of 3 letters or fewer that is not a part word or a
        trade abbreviation (_SHORT_OK): plate prefixes, short names; a
        single A-D stays only before "pillar" / "post".
    When in doubt a word goes: a name must never stay."""
    from app.parts.catalogue import _scrubbed

    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    raw = _scrubbed(raw, list(secrets)) if secrets else raw
    tokens = _TOKEN.findall(raw)
    keep = [True] * len(tokens)
    words = [_letters(t) for t in tokens]
    lowered = [t.lower().strip(".") for t in tokens]

    def drop_after(i: int) -> None:
        """The words after a title or a name link, up to the next part word
        (the first one goes whatever it is)."""
        j = i + 1
        while j < len(tokens):
            if j > i + 1 and _part_word(words[j]):
                break
            keep[j] = False
            j += 1

    for i in range(len(tokens) - 1):
        if f"{words[i]} {words[i + 1]}" in name_words:
            keep[i] = keep[i + 1] = False
    for i in range(len(tokens)):
        if words[i] in _TITLES:
            keep[i] = False
            drop_after(i)
        elif lowered[i] in _NAME_LINKS and 0 < i < len(tokens) - 1:
            keep[i] = False
            if not _part_word(words[i - 1]):
                keep[i - 1] = False
            drop_after(i)
    for i, token in enumerate(tokens):
        if not keep[i]:
            continue
        word = words[i]
        if "@" in token or any(ch.isdigit() for ch in token) or any(ord(ch) > 127 for ch in token):
            keep[i] = False
        elif not word or word in name_words:
            keep[i] = False
        elif len(word) == 1:
            keep[i] = word in "abcd" and i + 1 < len(tokens) and words[i + 1] in _PILLAR
        elif len(word) <= 3 and word not in _SHORT_OK:
            keep[i] = False
    name = normalise_item(" ".join(t for t, k in zip(tokens, keep, strict=True) if k))
    # A last guard: nothing with a digit, nothing short and unknown that
    # normalising split off ("L.H." is "l h").
    name = " ".join(w for w in name.split() if not any(ch.isdigit() for ch in w)
                    and (len(w) > 3 or w in _SHORT_OK or (len(w) == 1 and w in "abcd")))
    return name[:MAX_NAME].strip() or None


# ------------------------------------------------------------ the car


def area_of(part: str | None) -> str | None:
    """front | rear | side | roof | underbody | cabin | wheel, or None (an
    unnamed Other, a name nothing knows)."""
    if not part or part == OTHER_PART:
        return None
    end = end_of(part)
    if end:
        return end
    base = split_side(part)[0]
    if base in _AREA_OF:
        return _AREA_OF[base]
    return "side" if base in _BASES else None


def year_band(vehicle: dict) -> str | None:
    """"2015-19" from the registration date (else vehicle_info.year)."""
    for value in (vehicle.get("date_of_registration"), vehicle.get("year")):
        m = _YEAR.search(str(value or ""))
        if m:
            year = int(m.group(1))
            if year > datetime.now(timezone.utc).year + 1:
                continue
            start = year - year % 5
            return f"{start}-{str(start + 4)[-2:]}"
    return None


def impact_of(placed: list[tuple[str | None, str | None]]) -> tuple[str | None, str | None]:
    """(end, side) of the hit from the signed-off parts' (area, side):
    end front / rear when the parts at an end are all at that one, "both"
    when at both, else "side" (doors, sills, wheels), "other" (roof,
    cabin, underbody); side left / right / both, "centre" when no part has
    a side. (None, None) when nothing says."""
    areas = [a for a, _ in placed if a]
    sides = {s.lower() for _, s in placed if s}
    ends = {a for a in areas if a in ("front", "rear")}
    if len(ends) == 2:
        end = "both"
    elif ends:
        end = next(iter(ends))
    elif any(a in ("side", "wheel") for a in areas):
        end = "side"
    elif areas:
        end = "other"
    else:
        end = None
    if end is None and not sides:
        return None, None
    side = "both" if len(sides) == 2 else next(iter(sides)) if sides else "centre"
    return end, side


def vehicle_fields(data: dict, ai_draft) -> dict:
    """make and model key as the price book keys them (prices.price_key:
    history.split_make_model, history.model_key with twins merged) -- each
    kept when known, the make without a model too; body type; year band;
    whether a person confirmed make and model, and the point of impact."""
    vehicle = data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {}
    raw = " ".join(str(v).strip() for v in (vehicle.get("make"), vehicle.get("model")) if v and str(v).strip())
    make, model = split_make_model(raw)
    body = body_type_for(vehicle.get("make"), vehicle.get("model"), vehicle.get("body_type"))
    draft = ai_draft if isinstance(ai_draft, dict) else None
    return {
        "make": make[:40] or None,
        "model_key": model_key(model)[:40] or None,
        "body_type": body if body in BODY_TYPES else None,
        "year_band": year_band(vehicle),
        "vehicle_confirmed": person_confirmed(data, draft, "vehicle_info.make")
        and person_confirmed(data, draft, "vehicle_info.model"),
        "impact_confirmed": person_confirmed(data, draft, "vehicle_info.point_of_impact"),
    }


# ------------------------------------------------------------ the part


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def part_fields(item: dict, secrets=(), name_words=()) -> dict:
    """The part as a row names it: part, family, side, area, other_name."""
    part = str(item.get("part") or "").strip()
    other = other_part_name(item)
    if part != OTHER_PART and not known_part(part):
        # A name from before the taxonomy ("Tail lamp LH"): its canonical
        # part when the past-line rules read it as one, else an Other.
        canon = canonical_part(part)
        part, other = (canon, None) if canon else (OTHER_PART, part)
    side_known = not side_unconfirmed(item)
    if part == OTHER_PART:
        name = scrub_words(other, secrets, name_words) if other else None
        family = family_of(name) if name else None
        side = side_of(other) if other and side_known else None
        return {"part": OTHER_PART, "family": family, "side": side, "area": area_of(family), "other_name": name}
    family, side = family_and_side(part)
    return {"part": part, "family": family, "side": side if side_known else None, "area": area_of(part),
            "other_name": None}


def condition_words(item: dict) -> list[str]:
    """The firm's words on an item (condition tokens and condition), and a
    whole condition (Necessary / Serviceable / Repair) -- known words only."""
    words = condition_tokens_of(item.get("condition_tokens") or [])
    condition = item.get("condition")
    if isinstance(condition, str) and condition.strip():
        words += [t for t in condition_tokens_of(condition) if t not in words]
        whole = next((w for w in FIRM_WHOLE_CONDITIONS if w.lower() == condition.strip().lower()), None)
        if whole and whole not in words:
            words.append(whole)
    return words[:MAX_WORDS]


def _action(item: dict) -> str | None:
    text = str(item.get("remarks") or "").strip().lower()
    return next((r for r in FIRM_REMARKS if r.lower() == text), None)


def _hit(item: dict) -> str | None:
    try:
        return hit_of(item)
    except Exception:
        return None


def _reads(item: dict) -> list[str]:
    reads = [str(r)[:40] for r in item.get("found_by") or [] if isinstance(r, str) and r.strip()]
    return list(dict.fromkeys(reads))[:MAX_READS]


def _ai_how(item: dict) -> str:
    reads = _reads(item)
    return "zone" if reads and all(r.startswith("zone") for r in reads) else "ai_read"


def _rule(fact: dict) -> str | None:
    rule = fact.get("link_rule")
    return rule.strip()[:MAX_RULE] if isinstance(rule, str) and rule.strip() else None


def _suggested_how(fact: dict) -> tuple[str, str | None, str | None]:
    """(how, source, link_rule) of a suggested part."""
    source = fact.get("source") if fact.get("source") in SUGGESTION_SOURCES else None
    rule = _rule(fact)
    if source == "link":
        rule_id = rule.split(":", 1)[1] if rule and ":" in rule else (rule or "")
        return ("catalogue" if rule_id.startswith(CATALOGUE_RULE) else "link"), source, rule
    return "suggestion", source, None


def own_wordings(key: str) -> set[str]:
    """The wording keys (history.normalise_item) of a part's own Annex A
    line: its name, and the firm's wording of it on any body ("Rear bumper
    fascia" -> "rear bumper"); a named Other's, its name."""
    if key.startswith(OTHER_PART + ":"):
        names = [key[len(OTHER_PART) + 1:]]
    else:
        names = [key, FIRM_PART_NAMES.get(key), FIRM_PART_NAMES_HATCH.get(key)]
    return {normalise_item(n) for n in names if n} - {""}


def fittings_of(key: str, annex_rows: list[dict], secrets=(), name_words=()) -> list[dict]:
    """The Annex A lines renewed with the part `key` (their source_part is
    it), not its own line -- marked main, or, on a row from before that
    mark (8 Nov 2026), worded as the part itself (own_wordings): wording
    key, qty, section, Necessary."""
    out = []
    own = own_wordings(key) if key else set()
    for row in annex_rows:
        source = row.get("source_part")
        if row.get("main") or not isinstance(source, str) or not source.strip():
            continue
        if item_key({"part": source}) != key or normalise_item(str(row.get("part_name") or "")) in own:
            continue
        wording = scrub_words(row.get("part_name"), secrets, name_words)
        if not wording:
            continue
        try:
            qty = round(float(row.get("qty") or 1.0), 2)
        except (TypeError, ValueError):
            qty = 1.0
        out.append({
            "wording": wording,
            "qty": qty,
            "section": "nett" if row.get("is_net_item") else "list",
            "necessary": str(row.get("condition") or "").strip().lower() == "necessary",
        })
    return out[:MAX_FITTINGS]


def _row(item: dict, *, outcome: str, how: str, source=None, link_rule=None, found_by=(), prompted=False,
         fittings=(), secrets=(), name_words=()) -> dict:
    return {
        **part_fields(item, secrets, name_words),
        "kind": "part",
        "component_key": None,
        "condition_words": condition_words(item),
        "hit": _hit(item),
        "action": _action(item),
        "fittings": list(fittings),
        "how": how,
        "source": source,
        "link_rule": link_rule,
        "found_by": list(found_by)[:MAX_READS],
        "outcome": outcome,
        "prompted": bool(prompted),
    }


# ------------------------------------------------------------ a case


def observe(data: dict | None, ai_draft: dict | None, secrets=(), name_words=()) -> list[dict]:
    """The part rows of a case as signed off (see the module's docstring),
    without the case's fields. Pure: no database, no AI."""
    final_data = data if isinstance(data, dict) else {}
    ai = ai_draft if isinstance(ai_draft, dict) else {}
    ai_parts, final = _parts(ai), _parts(final_data)
    pairs, removed, added = _pair_parts(ai_parts, final)
    annex = _rows(final_data)

    look = final_data.get("second_look") if isinstance(final_data.get("second_look"), dict) else {}
    items = [it for it in _dicts(look.get("items")) if key_of(it)]
    decisions = [d for d in _dicts(look.get("decisions")) if d.get("key")]
    dismissed = {str(k) for k in look.get("dismissed") or [] if isinstance(k, str) and k}
    still = standing(decisions)
    facts: dict[str, dict] = {}
    for it in items:
        facts.setdefault(key_of(it), it)
    for it in _dicts(ai.get("zone_suggestions")):
        if key_of(it):
            facts.setdefault(key_of(it), {**it, "source": it.get("source") or "zone"})

    def prompted(key: str, it: dict) -> bool:
        return it.get("added_from") == "suggestion" or any(d.get("action") == "added" for d in still.get(key) or [])

    kw = {"secrets": secrets, "name_words": name_words}
    rows: list[dict] = []
    done: set[str] = set()
    for a, f, _ in pairs:
        it, found = final[f], ai_parts[a]
        rows.append(_row(it, outcome="kept", how=_ai_how(found), found_by=_reads(found), prompted=prompted(f, it),
                         fittings=fittings_of(f, annex, **kw), **kw))
        done.update((a, f))
    for f in added:
        it = final[f]
        if prompted(f, it):
            fact = facts.get(f) or it
            how, source, rule = _suggested_how(fact)
            reads = _reads(fact) or _reads(it)
        else:
            how, source, rule, reads = "hand_added", None, None, []
        rows.append(_row(it, outcome="added", how=how, source=source, link_rule=rule, found_by=reads,
                         prompted=prompted(f, it), fittings=fittings_of(f, annex, **kw), **kw))
        done.add(f)
    for a in removed:
        found = ai_parts[a]
        rows.append(_row(found, outcome="removed", how=_ai_how(found), found_by=_reads(found), **kw))
        done.add(a)
    # The suggestions people saw that are not on the list: their keys, of
    # the decisions on them (not an undo) and of the dismissed list, once.
    shown: list[str] = []
    for key in [key_of(it) for it in items] + [str(d["key"]) for d in decisions if d.get("action") != "undone"] \
            + sorted(dismissed):
        if key not in shown and key not in done:
            shown.append(key)
    for key in shown:
        fact = facts.get(key)
        if fact is None:
            # Only a decision or the dismissed list names it: its part, or
            # a named Other's key ("Other / Not Listed: rear air vent").
            part = next((d.get("part") for d in decisions if str(d.get("key")) == key and d.get("part")), None)
            if key.startswith(OTHER_PART + ":"):
                fact = {"part": OTHER_PART, "other_part_name": key[len(OTHER_PART) + 1:]}
            else:
                fact = {"part": part or key}
        how, source, rule = _suggested_how(fact)
        is_dismissed = key in dismissed or any(d.get("action") == "dismissed" for d in still.get(key) or [])
        rows.append(_row(fact, outcome="dismissed" if is_dismissed else "left", how=how, source=source,
                         link_rule=rule, found_by=_reads(fact), **kw))
    return rows


def case_rows(db: Session, report: Report) -> list[dict]:
    """observe() for a stored, signed-off case, each row with the case's
    fields: ready for PartObservation(**row)."""
    data = report.data if isinstance(report.data, dict) else {}
    ai = report.ai_draft if isinstance(report.ai_draft, dict) else None
    secrets, name_words = case_secrets(report, db)
    rows = observe(data, ai, secrets, name_words)
    placed = [(r["area"], r["side"]) for r in rows if r["outcome"] in ("kept", "added")]
    end, side = impact_of(placed)
    sign_off = data.get("sign_off") if isinstance(data.get("sign_off"), dict) else {}
    signer = sign_off.get("signed_by_user_id")
    # From the case's own AI runs, as data.knowledge is made at filing and
    # sign-off (knowledge.case_stamp) -- the backfill too: never data.
    # knowledge as stored, which an older sign-off may have stamped wrong.
    try:
        stamp = knowledge.case_stamp(db, report.id)
    except Exception as exc:  # the rows matter more than their stamp
        logger.warning("Part observations of case %s: no knowledge stamp (%s)", report.id, type(exc).__name__)
        stamp = None
    case = {
        "report_id": report.id,
        "signed_off_at": signed_off_at(report),
        "signed_by": str(signer)[:40] if signer else None,
        **vehicle_fields(data, ai),
        "impact_end": end,
        "impact_side": side,
        "ai_drafted": bool(ai),
        "knowledge": stamp,
        "version": VERSION,
    }
    return [{**case, **row} for row in rows]


# ---------------------------------------------------------- recording


def forget(db: Session, report_id: str) -> int:
    """Drops the case's rows (reopened, deleted, or about to be captured
    again). The caller commits."""
    return db.query(PartObservation).filter(PartObservation.report_id == report_id).delete(synchronize_session=False)


def capture(db: Session, report_id: str, signature_date: str | None = None) -> int | None:
    """Writes the case's rows, replacing an earlier sign-off's; commits.
    Returns how many, or None when nothing was written: the case is no
    longer signed off by this sign-off (signature_date; None: whichever it
    is now -- the backfill). As accuracy.record: the work first, outside
    any write; then the old rows are deleted -- the first write, which
    takes SQLite's write lock -- and the case read again, so a reopen or
    another sign-off committed meanwhile wins and nothing is written."""
    report = db.get(Report, report_id)
    if not signed_off_as(report, signature_date):
        return None
    pinned = _signature(report)
    rows = case_rows(db, report)
    db.rollback()
    forget(db, report_id)
    db.expire_all()
    report = db.get(Report, report_id)
    if not signed_off_as(report, pinned):
        db.rollback()
        return None
    db.add_all(PartObservation(**row) for row in rows)
    db.commit()
    return len(rows)


def _years_before(now: datetime, years: int) -> datetime:
    try:
        return now.replace(year=now.year - years)
    except ValueError:  # 29 February
        return now.replace(year=now.year - years, day=28)


def sweep_old(db: Session, now: datetime | None = None) -> int:
    """Drops the rows signed off more than RETAIN_YEARS ago, and the rows of
    a case that is gone or no longer signed off (a reopen or delete whose
    forget() didn't get done). The caller commits. Returns how many."""
    now = now or datetime.now(timezone.utc)
    cutoff = _years_before(now.astimezone(timezone.utc).replace(tzinfo=None), RETAIN_YEARS)
    n = db.query(PartObservation).filter(PartObservation.signed_off_at < cutoff).delete(synchronize_session=False)
    signed = select(Report.id).where(Report.status == "Signed Off")
    n += db.query(PartObservation).filter(PartObservation.report_id.not_in(signed)).delete(synchronize_session=False)
    return n


def captured_cases(db: Session) -> set[str]:
    """The report ids that have rows now."""
    return {rid for (rid,) in db.query(PartObservation.report_id).distinct()}


# ------------------------------------------------------------ summary


def _counts() -> dict:
    return {"cases": 0, "kept": 0, "removed": 0, "added": 0, "added_prompted": 0, "dismissed": 0, "left": 0}


def part_label(part: str, family: str | None, other_name: str | None) -> str:
    """How the summary names a part: its family (both sides together), or
    "Other / Not Listed: <name>"."""
    if part == OTHER_PART:
        return f"{OTHER_PART}: {other_name}" if other_name else OTHER_PART
    return family or part


def summarize(db: Session, start: datetime | None = None, end: datetime | None = None, top: int = 50) -> dict:
    """Counts of what people changed, for GET /admin/observations/summary:
    over the cases signed off now, from `start` to before `end` (naive
    UTC), on the cases the AI drafted (a case without an AI draft is
    counted apart: every part on it is a person's). Totals, and the same
    per part, per make and per area -- counts only: no case, no name."""
    signed = select(Report.id).where(Report.status == "Signed Off")
    q = db.query(
        PartObservation.report_id, PartObservation.make, PartObservation.area, PartObservation.part,
        PartObservation.family, PartObservation.other_name, PartObservation.outcome, PartObservation.prompted,
        PartObservation.ai_drafted,
    ).filter(PartObservation.report_id.in_(signed))
    if start is not None:
        q = q.filter(PartObservation.signed_off_at >= start)
    if end is not None:
        q = q.filter(PartObservation.signed_off_at < end)

    totals = {**_counts(), "parts": 0}
    groups: dict[str, dict[str, dict]] = {"part": {}, "make": {}, "area": {}}
    cases: dict[str, dict[str, set]] = {"part": {}, "make": {}, "area": {}}
    all_cases: set[str] = set()
    no_draft: set[str] = set()
    for report_id, make, area, part, family, other_name, outcome, was_prompted, ai_drafted in q:
        if not ai_drafted:
            no_draft.add(report_id)
            continue
        if outcome not in OUTCOMES:
            continue
        all_cases.add(report_id)
        names = {"part": part_label(part, family, other_name), "make": make or "Unknown", "area": area or "unknown"}
        tallies = [totals]
        for group, name in names.items():
            tallies.append(groups[group].setdefault(name, _counts()))
            cases[group].setdefault(name, set()).add(report_id)
        for tally in tallies:
            tally[outcome] += 1
            if outcome == "added" and was_prompted:
                tally["added_prompted"] += 1
        totals["parts"] += 1
    totals["cases"] = len(all_cases)
    totals["cases_without_ai_draft"] = len(no_draft - all_cases)

    def listed(group: str, field: str) -> list[dict]:
        out = []
        for name, counts in groups[group].items():
            counts["cases"] = len(cases[group][name])
            out.append({field: name, **counts})
        out.sort(key=lambda r: (-(r["added"] + r["removed"] + r["dismissed"]), -r["cases"], r[field]))
        return out[:top]

    return {"totals": totals, "by_part": listed("part", "part"), "by_make": listed("make", "make"),
            "by_area": listed("area", "area")}


def outcome_counts(rows: list[dict]) -> Counter:
    """{outcome: n} of rows (the backfill's report)."""
    return Counter(r.get("outcome") for r in rows)

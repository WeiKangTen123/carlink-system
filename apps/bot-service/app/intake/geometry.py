"""E7 (plans/2026-11-22-enhance-staging 3c, Wave 2): the car's side worked
out in code, never named by the model.

settings.intake_side_geometry "vote", "tiebreak" or "shadow" asks the sort
(ai/triage.py) three things per photo -- none of them a left or a right:

  view            which part of the insured car the photo looks at: front
                  (from ahead), rear (from behind), side (one whole side,
                  from beside the car), corner (an end and a side), unknown;
  angle           the sort's own field, already asked today and thrown
                  away: which way the car's front points -- "front toward
                  camera", "front away from camera", "front to photo left",
                  "front to photo right", or null;
  steering_wheel  not_seen; photo_left / photo_right (through the
                  windscreen or rear window, on that half of the photo);
                  near_side (through a side window of the side facing the
                  camera); far_side (across the cabin).

photo_side() turns ONE photo into the car's side. Only a whole-side view
names one:
  - the car's front to the photo's left: the camera sees its LEFT side; to
    the photo's right: its RIGHT side (stand beside a car facing north on
    its west side, looking east: its front is on your left);
  - the steering wheel through the near side's window: the RIGHT side;
    across the cabin: the LEFT side -- Malaysian and Singapore cars are
    right-hand drive, the wheel is on the car's right;
  - both clues and they disagree: none; one clue alone: that side;
  - a "side" view whose angle says the front points toward or away from
    the camera contradicts itself: none, whatever the wheel (the review's
    L2).
Front, rear, corner and unknown views name no side: an end view shows both
sides, and a corner is the noisy case (the plan: corners stay
undetermined). On an end view the wheel only checks the angle (rhd_check):
toward the camera it shows on the photo's left, away on the photo's right.

Only photos of the insured car's outside count (votes(): kind damage,
overview, tyre, other; never an excluded or unusable one).

Where it is used (the only places today's code decides a part's side from
the reads), each by the setting:
  - the side zone reads (zone_read.parse_answer, before a find is named;
    the review's M4): the zone's geometry side is the side its photos'
    geometry names only when they are UNANIMOUS (unanimous(): at least one
    says a side and none says the other). Then the model's side_seen and
    that side are weighed by reconcile(): "vote" -- the two agree, or the
    model said "unclear": that side; they disagree: "unclear" (the "(side
    undetermined)" names); "tiebreak" -- used only when the model said
    "unclear". When the zone's photos are split, the zone keeps the
    model's side_seen, and each find whose own photo has a geometry side is
    named by reconcile(the model's, that photo's side); a find on a photo
    with none keeps the model's -- an "unclear" zone stays undetermined, it
    never becomes the majority side for every find.
  - the side resolver (pipeline.resolve_sides) placing a "(side
    undetermined)" list part: area_sides() -- the photo's OWN geometry side
    when it has one, else the majority (majority(): the side more of them
    say; a tie or none, None) of its sorter-area group (each of the
    sorter's areas -- left, front-left, rear ... -- its own group); a photo
    that doesn't vote gets none (the review's L1). "vote": one more clue
    beside the photo's own area, its neighbours and the reader's sided
    finds in the same zone (the existing rules then decide: a photo's own
    area side stands only when no clue opposes it; otherwise a clear 2:1
    majority). "tiebreak": only where those rules leave the side
    undetermined (no clue, a tie, a split, a clue against the photo's own
    area), the geometry side is placed.
"shadow" (the review's M6): the sort is asked and IntakePhoto.geometry
stored exactly as for vote / tiebreak (the same request, the same sort
cache), the side zones' run-log entries say what the geometry is, and
nothing uses it -- so a base arm can share the E7 arms' sort.
"off" (the default): the sort is asked nothing new and nothing here runs.
"""
from collections import Counter

from app.config import settings

VIEWS = ("front", "rear", "side", "corner", "unknown")
WHEELS = ("not_seen", "photo_left", "photo_right", "near_side", "far_side")
# triage.ANGLES' values (repeated here so this module imports no AI code).
TOWARD, AWAY = "front toward camera", "front away from camera"
TO_LEFT, TO_RIGHT = "front to photo left", "front to photo right"
ANGLES = (TOWARD, AWAY, TO_LEFT, TO_RIGHT)
# A whole-side view: the side the car's front points to names the side seen.
SIDE_BY_ANGLE = {TO_LEFT: "Left", TO_RIGHT: "Right"}
# Right-hand drive: the wheel behind the near window is on the car's right.
SIDE_BY_WHEEL = {"near_side": "Right", "far_side": "Left"}
# An end view's expected wheel (right-hand drive): the car's right is on the
# photo's left from ahead, on its right from behind.
WHEEL_EXPECTED = {TOWARD: "photo_left", AWAY: "photo_right"}
# The sort's kinds whose photos vote: the insured car's outside.
VOTING_KINDS = frozenset({"damage", "overview", "tyre", "other"})
MODES = ("off", "shadow", "vote", "tiebreak")
# The modes that use the geometry (shadow only asks for it and keeps it).
ACTING = ("vote", "tiebreak")


def mode() -> str:
    """settings.intake_side_geometry: "off", "shadow", "vote" or "tiebreak"."""
    value = getattr(settings, "intake_side_geometry", "off") or "off"
    return value if value in MODES else "off"


def on() -> bool:
    """The sort answers the geometry (any mode but "off")."""
    return mode() != "off"


def acting() -> bool:
    """The geometry decides sides ("vote" or "tiebreak")."""
    return mode() in ACTING


def clean(item: dict) -> dict:
    """The sort's three answers for one photo as stored (IntakePhoto.
    geometry): view, angle and wheel within their vocabularies (anything
    else: unknown / None / not_seen), with the side photo_side gives and
    the end view's rhd_check."""
    view = item.get("view") if item.get("view") in VIEWS else "unknown"
    angle = item.get("angle") if item.get("angle") in ANGLES else None
    wheel = item.get("steering_wheel") if item.get("steering_wheel") in WHEELS else "not_seen"
    out = {"view": view, "angle": angle, "wheel": wheel}
    out["side"] = photo_side(out)
    check = rhd_check(out)
    if check:
        out["rhd_check"] = check
    return out


def photo_side(geometry) -> str | None:
    """The car's side ONE photo shows, "Left" / "Right", or None (module
    docstring): a whole-side view by its angle and the steering wheel,
    None when they disagree; any other view, None."""
    if not isinstance(geometry, dict) or geometry.get("view") != "side":
        return None
    if geometry.get("angle") in (TOWARD, AWAY):
        return None  # a side view whose front faces the camera, or away: a contradiction (L2)
    by_angle = SIDE_BY_ANGLE.get(geometry.get("angle"))
    by_wheel = SIDE_BY_WHEEL.get(geometry.get("wheel"))
    if by_angle and by_wheel and by_angle != by_wheel:
        return None
    return by_angle or by_wheel


def rhd_check(geometry) -> str | None:
    """An end view seen through the glass: "ok" when the steering wheel is
    where a right-hand-drive car's would be for its angle, "conflict" when
    on the other half (a mistaken angle, or an import); None otherwise. For
    the log only: an end view names no side."""
    if not isinstance(geometry, dict) or geometry.get("view") not in ("front", "rear"):
        return None
    expected = WHEEL_EXPECTED.get(geometry.get("angle"))
    wheel = geometry.get("wheel")
    if not expected or wheel not in ("photo_left", "photo_right"):
        return None
    return "ok" if wheel == expected else "conflict"


def votes(photo) -> bool:
    """Whether a photo's geometry counts: the insured car's outside (kind in
    VOTING_KINDS), not excluded, not unusable."""
    return (getattr(photo, "kind", None) in VOTING_KINDS and not getattr(photo, "excluded", False)
            and getattr(photo, "quality", None) != "unusable")


def photo_sides(photos) -> dict[str, str]:
    """{photo id: "Left" / "Right"} for every photo whose geometry names a
    side and that votes (IntakePhoto rows, or anything with their kind,
    quality, excluded and geometry)."""
    out = {}
    for p in photos:
        side = photo_side(getattr(p, "geometry", None)) if votes(p) else None
        if side:
            out[p.id] = side
    return out


def tally(sides) -> dict[str, int]:
    """{"Left": n, "Right": m} of the sides given (None ignored)."""
    counts = Counter(s for s in sides if s in ("Left", "Right"))
    return {"Left": counts["Left"], "Right": counts["Right"]}


def majority(sides) -> str | None:
    """The side more of `sides` say; None on a tie or with none."""
    counts = tally(sides)
    if counts["Left"] == counts["Right"]:
        return None
    return "Left" if counts["Left"] > counts["Right"] else "Right"


def unanimous(sides) -> str | None:
    """The one side all of `sides` that name one say (None ignored); None
    when they are split or none names one (the review's M4)."""
    named = {s for s in sides if s in ("Left", "Right")}
    return named.pop() if len(named) == 1 else None


def area_sides(photos) -> dict[str, str]:
    """{photo id: its geometry side for the resolver} for every photo that
    votes: its own photo_side when it has one, else the majority of the
    voting photos with the same sorter area (camera order not needed).
    A photo that doesn't vote -- the other party's car, an excluded or
    unusable one -- is never in it (the review's L1)."""
    sides = photo_sides(photos)
    groups: dict[str, list[str]] = {}
    for p in photos:
        if getattr(p, "area", None) and votes(p):
            groups.setdefault(p.area, []).append(sides.get(p.id))
    by_area = {area: majority(found) for area, found in groups.items()}
    out = {}
    for p in photos:
        if not votes(p):
            continue
        side = sides.get(p.id) or (by_area.get(p.area) if getattr(p, "area", None) else None)
        if side:
            out[p.id] = side
    return out


def reconcile(model: str, geo: str | None, how: str) -> str:
    """A side zone's side_seen ("left", "right", "unclear") once the
    geometry majority `geo` ("Left" / "Right" / None) is weighed by `how`
    (module docstring)."""
    if how not in ("vote", "tiebreak") or geo not in ("Left", "Right"):
        return model
    geo_seen = geo.lower()
    if model not in ("left", "right"):
        return geo_seen
    if how == "vote" and model != geo_seen:
        return "unclear"
    return model


def resolve(today: str | None, geo: str | None, how: str) -> str | None:
    """The side resolver's tie-break: today's answer when it has one, else
    the geometry side ("tiebreak" only)."""
    if how == "tiebreak" and today is None and geo in ("Left", "Right"):
        return geo
    return today

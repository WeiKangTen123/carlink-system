"""E7 (plans/2026-11-22-enhance-staging 3c, Wave 2): the car's side worked
out in code, never named by the model.

settings.intake_side_geometry "vote" or "tiebreak" asks the sort (ai/
triage.py) three things per photo -- none of them a left or a right:

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
  - both clues and they disagree: none; one clue alone: that side.
Front, rear, corner and unknown views name no side: an end view shows both
sides, and a corner is the noisy case (the plan: corners stay
undetermined). On an end view the wheel only checks the angle (rhd_check):
toward the camera it shows on the photo's left, away on the photo's right.

majority() over a group of photos: the side more of them say; a tie, or
no photo with a side, is None. area_sides() takes it per sorter area (each
of the sorter's areas -- left, front-left, rear ... -- is its own group;
only photos of the insured car's outside count: damage, overview, tyre,
other; never an excluded or unusable one).

Where it is used (the only places today's code decides a part's side from
the reads), each by the setting:
  - the side zone reads (zone_read.parse_answer, before a find is named):
    the zone's side_seen against the majority over the photos it was sent.
    "vote": the model and the geometry are two votes -- they agree, or one
    of them abstains (unclear / no side), and that side stands; they
    disagree, and the side is "unclear" (the "(side undetermined)" names).
    "tiebreak": the geometry is used only when the model said "unclear".
  - the side resolver (pipeline.resolve_sides) placing a "(side
    undetermined)" list part: the majority of its photo's area group.
    "vote": one more clue beside the photo's own area, its neighbours and
    the reader's sided finds in the same zone (the existing rules then
    decide: a photo's own area side stands only when no clue opposes it;
    otherwise a clear 2:1 majority). "tiebreak": only where those rules
    leave the side undetermined (no clue, a tie, a split, a clue against
    the photo's own area), the geometry side is placed.
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
MODES = ("off", "vote", "tiebreak")


def mode() -> str:
    """settings.intake_side_geometry: "off", "vote" or "tiebreak"."""
    value = getattr(settings, "intake_side_geometry", "off") or "off"
    return value if value in MODES else "off"


def on() -> bool:
    return mode() != "off"


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


def area_sides(photos) -> dict[str, str]:
    """{photo id: its area group's majority side} for every photo whose
    sorter area has one: the photos with that same area (camera order not
    needed) that vote, by photo_side."""
    sides = photo_sides(photos)
    groups: dict[str, list[str]] = {}
    for p in photos:
        if getattr(p, "area", None):
            groups.setdefault(p.area, []).append(sides.get(p.id))
    by_area = {area: majority(found) for area, found in groups.items()}
    return {p.id: by_area[p.area] for p in photos if getattr(p, "area", None) and by_area.get(p.area)}


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

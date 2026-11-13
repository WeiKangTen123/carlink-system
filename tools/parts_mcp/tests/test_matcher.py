"""find_diagrams scoring rules, the synonym table and the Source hooks (offline).

Diagram names come from the saved fixture pages (Honda Civic FD type page, BMW X1
groups) and, for the synonym table, from other makers' catalogues (Toyota, Nissan,
Mazda, Hyundai, Honda Japan) as plain strings.
"""

from __future__ import annotations

import pytest

from parts_mcp import synonyms
from parts_mcp.core.models import Diagram, Vehicle
from parts_mcp.core.store import open_store
from parts_mcp.service import CatalogueService
from parts_mcp.sources import microfiches as mf
from parts_mcp.sources.fake import FakeSource

from .conftest import HONEST, FakeTransport, resp
from .test_microfiches import ROUTES, page


@pytest.fixture
def civic_svc(make_fetcher, clock):
    transport = FakeTransport({url: resp(200, page(name), content_type="text/html; charset=UTF-8")
                               for url, name in ROUTES.items()})
    source = mf.MicrofichesSource(make_fetcher(transport, HONEST))
    store = open_store(":memory:")
    svc = CatalogueService(store, [source], routes={"HONDA": ["microfiches"]}, default_route=["microfiches"],
                           now=clock.now)
    civic = svc.find_vehicles("HONDA", "Civic 1.8 EXi automatic Singapore", 2008).value[0]
    yield svc, civic
    store.close()


def top(svc, vehicle, area: str, n: int = 2) -> list[str]:
    """What an evaluation takes: the first n matches scoring 0.5 or more."""
    return [m.diagram.name for m in svc.find_diagrams(vehicle, area).matches[:n] if m.score >= 0.5]


# --- rule (a): the diagram's own name decides, never the group's ----------------------------------


def test_group_words_do_not_lift_diagrams(civic_svc):
    svc, civic = civic_svc
    # the group is "ELECTRICAL EQUIPMENTS, EXHAUST, HEATER": AIR CLEANER used to score 1.0
    assert top(svc, civic, "exhaust") == ["EXHAUST PIPE / SILENCER"]
    # the group is "STEERING, BRAKE, SUSPENSION": FRONT DRIVESHAFT / FRONT BRAKE used to score 1.0
    found = svc.find_diagrams(civic, "front suspension")
    assert [m.diagram.name for m in found.matches[:2]] == ["KNUCKLE", "FRONT SUB FRAME"]
    assert all(m.score < 0.5 for m in found.matches if m.diagram.name in ("FRONT DRIVESHAFT (AT)", "FRONT BRAKE (1)"))


def test_group_name_alone_matches_nothing(clock):
    store = open_store(":memory:")
    svc = CatalogueService(store, [FakeSource(now=clock.now)], default_route=("fake",), now=clock.now)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    found = svc.find_diagrams(v, "illuminazione")  # the Lighting group's Italian name
    assert "63" in {g.id for g in found.searched_groups}
    assert found.matches == []
    store.close()


# --- rule (b): surveyor words reach the catalogue names ------------------------------------------


@pytest.mark.parametrize("area, diagram", [
    ("exhaust", "EXHAUST PIPE / SILENCER"), ("silencer", "EXHAUST PIPE / SILENCER"),
    ("muffler", "EXHAUST PIPE / SILENCER"),
    ("front suspension", "KNUCKLE"), ("lower arm", "KNUCKLE"), ("anti-roll bar", "KNUCKLE"),
    ("stabilizer", "KNUCKLE"), ("stabiliser link", "KNUCKLE"),
    ("cross member", "FRONT SUB FRAME"), ("stabilizer", "FRONT SUB FRAME"),
    ("steering rack", "P.S. GEAR BOX (HPS)"), ("steering gearbox", "P.S. GEAR BOX (HPS)"),
    ("spare tyre panel", "FLOOR / INNER PANELS"), ("boot floor", "FLOOR / INNER PANELS"),
    ("rear floor", "REAR TRAY / TRUNK LINING"), ("spare tire", "TOOLS / JACK"),
    ("badge", "EMBLEMS / CAUTION LABELS"), ("trunk lid", "EMBLEMS / CAUTION LABELS"),
    ("mudflap", "MUDGUARD"), ("rear fender", "MUDGUARD"), ("moulding", "MOLDING"),
    ("rear fender", "OUTER PANELS / REAR PANEL"), ("quarter panel", "OUTER PANELS / REAR PANEL"),
    ("rear end panel", "OUTER PANELS / REAR PANEL"),
    ("licence plate lamp", "TAILLIGHT / LICENSE LIGHT ('08)"),
    ("rear windscreen", "REAR WINDSHIELD / QUARTER GLASS"),
    ("rear door", "REAR DOOR PANELS"), ("front door", "FRONT DOOR GLASS / DOOR REGULATOR"),
    ("tyre", "TIRE / WHEEL DISKS"), ("rim", "TIRE / WHEEL DISKS"), ("wheel", "TIRE / WHEEL DISKS"),
    ("splash shield", "FRONT FENDERS"), ("wheel arch liner", "FRONT FENDERS"),
])
def test_civic_area_words_reach_honda_diagrams(civic_svc, area, diagram):
    svc, civic = civic_svc
    assert diagram in top(svc, civic, area)


@pytest.mark.parametrize("area, wrong", [
    ("rear panel", "REAR DOOR PANELS"),  # "rear ... panel" inside another area's name
    ("rear windshield", "WINDSHIELD WASHER (1)"),  # the front-windscreen concept is not selected
    ("rear windshield", "REAR DOOR GLASS / DOOR REGULATOR"),
    ("rear fender", "REAR WINDSHIELD / QUARTER GLASS"),
    ("wheel", "STEERING WHEEL (SRS)"),
    ("stabilizer", "REAR LOWER ARM"),  # a front-suspension synonym on a rear part
    ("rear bumper beam", "REAR LOWER ARM"),  # "rear beam" is the suspension's, not the bumper's
    ("rear bumper beam", "REAR SHOCK ABSORBER"),
])
def test_civic_no_wrong_pick(civic_svc, area, wrong):
    svc, civic = civic_svc
    assert wrong not in top(svc, civic, area)


def test_bumper_beam_lives_on_the_bumper_diagram(civic_svc):
    svc, civic = civic_svc
    assert top(svc, civic, "rear bumper beam")[0] == "BUMPERS ('08)"
    assert top(svc, civic, "front bumper beam")[0] == "BUMPERS ('08)"
    names = lambda q: {c.name for c in synonyms.matched_concepts(q)}  # noqa: E731
    assert names("rear bumper beam") == {"rear bumper beam"} and names("front bumper beam") == {"front bumper beam"}
    assert synonyms.score("rear bumper beam", "REAR BUMPER")[0] == 0.8
    assert synonyms.score("rear bumper beam", "REAR BUMPER BEAM")[0] == 1.0
    assert synonyms.score("rear bumper beam", "REAR AXLE")[0] < 0.5
    assert synonyms.score("rear bumper reinforcement", "Carrier, rear bumper")[0] >= 0.8


@pytest.mark.parametrize("query, name", [
    # Toyota category names
    ("cross member", "SUSPENSION CROSSMEMBER & UNDER COVER"), ("mudflap", "MUDGUARD & SPOILER"),
    ("badge", "EMBLEM & NAME PLATE (EXTERIOR & INTERIOR)"), ("rear floor", "FLOOR PAN & LOWER BACK PANEL"),
    ("rear windscreen", "ROOF PANEL & BACK WINDOW GLASS"), ("tailgate", "LUGGAGE COMPARTMENT DOOR & LOCK"),
    ("exhaust", "EXHAUST PIPE"), ("moulding", "SIDE MOULDING"), ("front door", "FRONT DOOR PANEL & GLASS"),
    # Nissan
    ("rear fender", "REAR FENDER & FITTING"), ("quarter panel", "BODY SIDE PANEL; REAR"),
    ("rear end panel", "REAR,BACK PANEL & FITTING"), ("moulding", "BODY SIDE MOULDING"),
    ("door regulator", "FRONT DOOR WINDOW & REGULATOR"),
    # Mazda
    ("tailgate", "LIFT GATE (5-DOOR)"), ("badge", "ORNAMENTS"), ("spare tyre panel", "FLOOR PANELS"),
    ("quarter panel", "SIDE PANELS (5-DOOR) [01/02]"),
    # Hyundai
    ("cross member", "FRONT SUSPENSION CROSSMEMBER"), ("trunk lid", "BACK PANEL & TRUNK LID"),
    ("silencer", "MUFFLER & EXHAUST PIPE"), ("splash shield", "UNDERCOVER"),
    # Honda Japan (Accord, HR-V)
    ("fender liner", "UNDER COVER/REAR INNER FENDER"), ("splash shield", "SIDE SILL GARNISH / UNDER COVER"),
    ("steering rack", "STEERING GEAR BOX (LH)"), ("stabilizer", "FRONT LOWER ARM / STABILIZER SPRING"),
    ("tailgate", "TAILGATE PANEL (2D)"), ("rear floor", "CARGO FLOOR"),
    # BMW (English names)
    ("badge", "Emblems / letterings"), ("wheel arch", "Wheelarch trim"), ("tailgate", "Trunk lid"),
    ("parking sensor", "Park Distance Control (PDC)"), ("moulding", "Roof moulding / Roof rail"),
])
def test_brand_names_reach_the_area(query, name):
    assert synonyms.score(query, name)[0] >= 0.8


# --- the scoring rules ------------------------------------------------------------------------------


@pytest.mark.parametrize("query, name", [
    ("exhaust", "SOLENOID VALVE EXHAUST CONTROL(1.5L)"),  # the word is incidental to the name
    ("exhaust", "EXHAUST MANIFOLD"),  # a more specific area (engine side)
    ("silencer", "ROOF HEADLINING & SILENCER PAD"),
    ("wheel", "STEERING WHEEL (SRS)"),
    ("rear panel", "REAR DOOR PANELS"),
    ("rear fender", "REAR WINDSHIELD / QUARTER GLASS"),
    ("rear windshield", "REAR DOOR GLASS / DOOR REGULATOR"),
    ("stabilizer", "REAR LOWER ARM"),
    ("rear door", "REAR BUMPER (4-DOOR) [01/02]"),  # a word only in the bracket text
    ("side panel", "INSTRUMENT PANEL GARNISH (DRIVER SIDE)"),
])
def test_weak_matches_stay_below_half(query, name):
    assert synonyms.score(query, name)[0] < 0.5


def test_full_scores_for_specific_names():
    assert synonyms.score("exhaust", "EXHAUST PIPE / SILENCER (1.5L)")[0] == 1.0
    assert synonyms.score("headlight", "HEADLIGHT (HALOGENE)")[0] == 1.0  # bracket text only qualifies
    assert synonyms.score("rear panel", "OUTER PANELS / REAR PANEL")[0] == 1.0  # one listed thing is enough
    assert synonyms.score("badge", "EMBLEMS / CAUTION LABELS")[0] == 0.9
    assert synonyms.score("stabilizer", "KNUCKLE")[0] == 0.9


def test_also_targets_rank_below_synonyms_and_keep_their_order():
    expansion = synonyms.expand("front suspension")
    weights = {p: w for p, w, _ in expansion}
    assert weights["front suspension"] == 1.0 and weights["knuckle"] == 0.9
    assert weights["front sub frame"] == weights["front shock absorber"] == 0.8
    sub = synonyms.rate("front suspension", "FRONT SUB FRAME", expansion)
    strut = synonyms.rate("front suspension", "FRONT SHOCK ABSORBER", expansion)
    assert sub.score == strut.score and sub.rank < strut.rank  # the sub frame wins the tie
    door = synonyms.expand("front door")
    assert [p for p, _, _ in door].index("door glass") < [p for p, _, _ in door].index("door lock")


def test_concept_selection():
    names = lambda q: {c.name for c in synonyms.matched_concepts(q)}  # noqa: E731
    assert names("rear fender") == {"rear quarter"}  # not "front fender" via "fender"
    assert "wheel" not in names("spare tire") and "spare tire" in names("spare tyre")
    assert names("rear windshield") == {"rear window"}  # not the windscreen via "windshield"
    assert names("front bumper") == {"front bumper"} and names("bumper") == {"rear bumper", "front bumper"}
    assert "front suspension" in names("lower arm")
    assert names("rear lower arm") == {"rear suspension"}
    assert names("steering wheel") == {"steering wheel"}
    assert names("rear windscreen glass moulding") >= {"rear window", "molding"}


def test_british_spellings_fold():
    assert synonyms.tokens("Tyre mouldings licence stabiliser disc windscreen") == [
        "tire", "molding", "license", "stabilizer", "disk", "windshield"]


def test_segments_ignore_brackets_and_fillers():
    assert synonyms.segments("EXHAUST PIPE / SILENCER (1.5L)") == [{"exhaust", "pipe"}, {"silencer"}]
    assert synonyms.segments("P.S. GEAR BOX COMPONENTS (HPS)") == [{"gear", "box"}]
    assert synonyms.segments("REAR,BACK PANEL & FITTING") == [{"back", "panel"}]


# --- Honda's wrapped names ------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, shown, fixed", [
    ("FRONT DOOR GLASS/DOOR REG ULATOR", "FRONT DOOR GLASS / DOOR REG ULATOR", "FRONT DOOR GLASS / DOOR REGULATOR"),
    ("SIDE SILL GARNISH/UNDER C OVER", "SIDE SILL GARNISH / UNDER C OVER", "SIDE SILL GARNISH / UNDER COVER"),
    ("BRAKE MASTER CYLINDER/MAS TER POWER (LH)", "BRAKE MASTER CYLINDER / MAS TER POWER (LH)",
     "BRAKE MASTER CYLINDER / MASTER POWER (LH)"),
    # a real word break at the wrap point: two spaces, or a bracket the site appended
    ("ENGINE ASSY./TRANSMISSION  ASSY. (1.5L)", "ENGINE ASSY. / TRANSMISSION ASSY. (1.5L)",
     "ENGINE ASSY. / TRANSMISSION ASSY. (1.5L)"),
    ("CAMSHAFT/CAM CHAIN (1.5L) (KE/KG)", "CAMSHAFT / CAM CHAIN (1.5L) (KE / KG)",
     "CAMSHAFT / CAM CHAIN (1.5L) (KE / KG)"),
    ("FRONT DOOR GLASS/ DOOR REGULATOR", "FRONT DOOR GLASS / DOOR REGULATOR", "FRONT DOOR GLASS / DOOR REGULATOR"),
    (None, "HEADLIGHT", "HEADLIGHT"),
])
def test_unwrap_honda_name(raw, shown, fixed):
    assert mf.unwrap_honda_name(raw, shown) == fixed


def test_honda_diagram_names_are_unwrapped(civic_svc):
    svc, civic = civic_svc
    group = next(g for g in svc.list_groups(civic).value if g.name.startswith("ELECTRICAL"))
    tank = next(d for d in svc.list_diagrams(civic, group).value if d.name.startswith("RADIATOR HOSE"))
    assert tank.name == "RADIATOR HOSE / RESERVE TANK"
    assert tank.notes == "name on site: RADIATOR HOSE / RESERVE TAN K"


# --- rule (c): the Source hooks ----------------------------------------------------------------------------


class HintingSource(FakeSource):
    """Files the luggage lid under "Vehicle trim" and calls the engine a "powerplant"."""

    def group_hints(self, vehicle: Vehicle, query: str) -> list[str]:
        return {"trunk lid": ["Vehicle trim"], "powerplant": ["Engine"]}.get(query, [])

    def diagram_matches(self, vehicle: Vehicle, query: str, diagram: Diagram) -> bool:
        return query == "powerplant" and diagram.name == "Short engine"


def test_source_group_hints_choose_groups_to_open(clock):
    store = open_store(":memory:")
    svc = CatalogueService(store, [HintingSource(now=clock.now)], default_route=("fake",), now=clock.now)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    found = svc.find_diagrams(v, "trunk lid", max_new_groups=1)
    assert [g.name for g in found.searched_groups] == ["Vehicle trim"]  # not "Bodywork"
    engine = svc.find_diagrams(v, "powerplant", max_new_groups=1)
    assert [(m.diagram.name, m.score, m.why) for m in engine.matches] == [
        ("Short engine", 0.9, "fake naming pattern")]
    store.close()


def test_default_hooks_add_nothing(clock):
    src = FakeSource(now=clock.now)
    assert src.group_hints(None, "rear bumper") == [] and src.diagram_matches(None, "rear bumper", None) is False


def test_source_hint_score_matches_hint_names_to_group_names():
    hints = ["BODY(SIDE & REAR)", "BODY(BACK DOOR & REAR BODY)", "ELECTRICAL"]
    assert synonyms.source_hint_score(hints, "BODY(SIDE & REAR)") == 1.0  # the first hint, exactly
    assert 0.9 <= synonyms.source_hint_score(hints, "BODY(BACK DOOR & REAR BODY)") < 1.0  # a later hint
    assert synonyms.source_hint_score(hints, "3. ELECTRICAL EQUIPMENTS,EXHAUST,HEATER") >= 0.85  # contained
    assert synonyms.source_hint_score(hints, "5. BODY(SIDE & REAR)") == 1.0  # leading number ignored
    assert synonyms.source_hint_score(hints, "ENGINE MECHANICAL") == 0.0

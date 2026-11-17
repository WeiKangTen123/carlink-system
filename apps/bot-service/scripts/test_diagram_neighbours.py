"""catalogue.diagram_neighbours: the parts the cached catalogue draws with a
damaged part (contract 10, detection's catalogue input).

    python scripts/test_diagram_neighbours.py

Checked: a cold cache gives {} (and queues nothing); the catalogue car is
the done look-up whose years cover the case's year, and with no year the
newest look-up's car; make and model are compared the way the case writes
them (case-insensitive, "HR-V" = "HRV"); only done jobs count; a neighbour
is listed once, in the order of first appearance, at most 8 a part; the end
filter (no rear neighbour for a front part, though the diagram holds both);
sides (a Left part gets Left neighbours, an unsided one gets "(side
undetermined)" ones); broken data and a failing database give {} and never
raise; and nothing here opens a socket or an httpx connection.

Offline and self-contained: a temporary database with made-up jobs and rows.
"""
import logging
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="neighbours-test-"))
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
(_tmp / "storage").mkdir()

# Any connection at all is a failure: this function reads the database and nothing else.
CONNECTIONS: list[str] = []
_real_connect = socket.socket.connect


def _refuse(sock, address):
    CONNECTIONS.append(f"socket {address!r}")
    raise ConnectionRefusedError(f"test is offline: {address!r}")


socket.socket.connect = _refuse
try:
    import httpx

    def _httpx_refuse(self, request, *args, **kwargs):
        CONNECTIONS.append(f"httpx {request.url}")
        raise AssertionError(f"httpx request to {request.url}")

    httpx.Client.send = _httpx_refuse
    httpx.AsyncClient.send = _httpx_refuse
except ImportError:  # the sockets are still guarded
    pass

from app.parts import catalogue  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import CatalogueJob, CatalogueSuggestion  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def parts_of(result: dict, part: str) -> list[str]:
    return [n["part"] for n in result.get(part, [])]


# ------------------------------------------------------------------ data


def add_job(report_id, *, status="done", brand="Honda", model_key="Civic", year=2008, vehicle=None, diagrams=()):
    """A look-up with its rows. diagrams: [(key, name, source_part, [row names])]."""
    db = SessionLocal()
    try:
        job = CatalogueJob(
            report_id=report_id, status=status, payload={"brand": brand, "model_key": model_key, "year": year, "parts": []},
            vehicle=vehicle, requested_by="test",
        )
        db.add(job)
        db.flush()
        for d_rank, (key, name, source_part, rows) in enumerate(diagrams):
            for r_rank, row in enumerate(rows):
                db.add(CatalogueSuggestion(
                    report_id=report_id, job_id=job.id, source_part=source_part, diagram_key=key, diagram_name=name,
                    diagram_rank=d_rank, row_rank=r_rank, name=row, source="microfiches",
                ))
        db.commit()
        return job.id
    finally:
        db.close()


CIVIC = {"key": "civic-a", "brand": "HONDA", "model": "CIVIC", "year_from": 2006, "year_to": 2011}
CIVIC_NEW = {"key": "civic-b", "brand": "HONDA", "model": "CIVIC", "year_from": 2012, "year_to": 2016}
BUMPERS = ("D1", "BUMPERS ('08)", "Rear Bumper", [
    "FACE, FR. BUMPER", "FACE, RR. BUMPER", "BEAM COMP., FR. BUMPER", "BEAM COMP., RR. BUMPER UPPER",
    "BASE, FR. LICENSE PLATE", "CLIP, BUMPER", "BOLT, FLANGE, 6X12", "FACE, FR. BUMPER",
])
TAIL = ("D2", "TAILLIGHT", "Left Tail Lamp", [
    "LAMP UNIT, R. TAIL", "LAMP UNIT, L. TAIL", "LAMP UNIT, R. LID", "LAMP UNIT, L. LID", "BULB, W5W",
])
TAIL_NEW = ("D3", "TAILLIGHT", "Left Tail Lamp", [
    "LAMP UNIT, R. TAIL", "LAMP UNIT, L. TAIL", "REFLECTER ASSY., R. RR.", "REFLECTER ASSY., L. RR.",
])


def setup() -> None:
    init_db()
    check(True, "database ready")


def cold() -> None:
    check(catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper"]) == {}, "no look-ups at all: {}")
    add_job("r-pending", status="pending", vehicle=CIVIC, diagrams=[BUMPERS])
    add_job("r-failed", status="failed", vehicle=CIVIC, diagrams=[BUMPERS])
    add_job("r-cancelled", status="cancelled", vehicle=CIVIC, diagrams=[BUMPERS])
    check(catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper"]) == {}, "pending, failed and cancelled jobs don't count")
    db = SessionLocal()
    try:
        before = db.query(CatalogueJob).count()
    finally:
        db.close()
    catalogue.diagram_neighbours("Honda", "Civic", None, ["Rear Bumper"])
    db = SessionLocal()
    try:
        check(db.query(CatalogueJob).count() == before, "it queues no job")
    finally:
        db.close()
    check(catalogue.diagram_neighbours(None, "Civic", 2008, ["Rear Bumper"]) == {}, "no make: {}")
    check(catalogue.diagram_neighbours("Honda", "", 2008, ["Rear Bumper"]) == {}, "no model: {}")
    check(catalogue.diagram_neighbours("Honda", "Civic", 2008, []) == {}, "no parts: {}")


def warm() -> None:
    add_job("r-old", vehicle=CIVIC, year=2008, diagrams=[BUMPERS, TAIL])
    add_job("r-new", vehicle=CIVIC_NEW, year=2014, diagrams=[TAIL_NEW])


def years() -> None:
    old = catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper", "Left Tail Lamp"])
    check("Rear Bumper Beam" in parts_of(old, "Rear Bumper"), "year 2008: the old car's bumper diagram", str(old))
    check("Left Boot Lid Lamp" in parts_of(old, "Left Tail Lamp"), "year 2008: the old car's lamp diagram", str(old))
    new = catalogue.diagram_neighbours("Honda", "Civic", 2014, ["Rear Bumper", "Left Tail Lamp"])
    check("Rear Bumper" not in new, "year 2014: the new car has no bumper diagram", str(new))
    check(parts_of(new, "Left Tail Lamp") == ["Left Rear Bumper Reflector"], "year 2014: the new car's lamp diagram", str(new))
    check(catalogue.diagram_neighbours("Honda", "Civic", 2020, ["Rear Bumper"]) == {}, "a year no car covers: {}")
    check(catalogue.diagram_neighbours("Honda", "Civic", 2011, ["Rear Bumper"]) != {}, "the last year of a car is covered")
    default = catalogue.diagram_neighbours("Honda", "Civic", None, ["Left Tail Lamp", "Rear Bumper"])
    check(parts_of(default, "Left Tail Lamp") == ["Left Rear Bumper Reflector"] and "Rear Bumper" not in default,
          "no year: the newest look-up's car (the default car)", str(default))
    names = catalogue.diagram_neighbours("HONDA", "civic 1.8", 2008, ["Rear Bumper"])
    check("Rear Bumper Beam" in parts_of(names, "Rear Bumper"), "make and model compare case-insensitively, by first word")
    check(catalogue.diagram_neighbours("BMW", "Civic", 2008, ["Rear Bumper"]) == {}, "another make: {}")
    check(catalogue.diagram_neighbours("Honda", "Jazz", 2008, ["Rear Bumper"]) == {}, "another model: {}")
    one = item = catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper"])["Rear Bumper"][0]
    check(set(one) == {"part", "oem_name", "diagram", "source"}, "each neighbour has part, oem_name, diagram, source")
    check(item["source"] == "microfiches" and item["diagram"] == "BUMPERS ('08)", "source and diagram name come from the rows")


def hr_v() -> None:
    add_job("r-hrv", brand="Honda", model_key="HR-V", year=2018, vehicle={"key": "hrv-a", "brand": "HONDA", "model": "HRV",
            "year_from": 2015, "year_to": 2021}, diagrams=[("H1", "TAILLIGHT", "Tail Lamp (side undetermined)", [
                "LAMP UNIT, R.", "LAMP UNIT, L.", "LIGHT ASSY., R. LID", "LIGHT ASSY., L. LID"])])
    got = catalogue.diagram_neighbours("Honda", "HR-V 1.5", 2018, ["Tail Lamp (side undetermined)"])
    check(parts_of(got, "Tail Lamp (side undetermined)") == ["Boot Lid Lamp (side undetermined)"],
          "HR-V = HRV; an unsided part gets one unsided neighbour, not a pair", str(got))


def end_filter() -> None:
    got = catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper", "Front Bumper"])
    rear, front = parts_of(got, "Rear Bumper"), parts_of(got, "Front Bumper")
    check(rear == ["Rear Bumper Beam"], "a rear bumper gets the rear beam only", str(rear))
    check(front == ["Front Bumper Beam", "Front Number Plate"], "a front bumper gets the front beam and plate only", str(front))
    check(not any(p.startswith("Front") for p in rear) and not any(p.startswith("Rear") for p in front),
          "no rear neighbour for a front part and the reverse")


def sides() -> None:
    got = catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Left Tail Lamp", "Right Tail Lamp", "Tail Lamp (side undetermined)"])
    check(parts_of(got, "Left Tail Lamp") == ["Left Boot Lid Lamp"], "Left gets Left", str(got))
    check(parts_of(got, "Right Tail Lamp") == ["Right Boot Lid Lamp"], "Right gets Right", str(got))
    check(parts_of(got, "Tail Lamp (side undetermined)") == ["Boot Lid Lamp (side undetermined)"], "no side: undetermined", str(got))


def dedupe_and_cap() -> None:
    names = [f"{n}, FR. BUMPER" for n in ("FACE",)] + [
        "BEAM COMP., FR. BUMPER", "BASE, FR. LICENSE PLATE", "GLASS SET, FR. WINDSHIELD", "PANEL R, FR FENDER",
        "PANEL L, FR FENDER", "GARNISH, R. FR. FENDER", "GARNISH, L. FR. FENDER", "PROTECTOR, R. FR. WHEEL A",
        "PROTECTOR, L. FR. WHEEL A", "BASE, FR. GRILLE", "SUB FRAME, FR.", "COVER, ENGINE UNDER",
        "BEAM COMP., R. FR. BUMPER UPPER", "BEAM COMP., L. FR. BUMPER UPPER",
    ]
    add_job("r-cap", brand="Toyota", model_key="Vios", year=2010, vehicle={"key": "vios", "brand": "TOYOTA", "model": "VIOS"},
            diagrams=[("C1", "FRONT END", "Front Bumper", names)])
    got = catalogue.diagram_neighbours("Toyota", "Vios", 2010, ["Front Bumper"])["Front Bumper"]
    parts = [n["part"] for n in got]
    check(len(parts) == 8, "at most 8 a part", str(parts))
    check(len(set(parts)) == len(parts), "each part once (the beam's two rows give one)")
    check(parts[:3] == ["Front Bumper Beam", "Front Number Plate", "Front Windscreen"], "in the order of first appearance", str(parts))
    check("Front Bumper" not in parts, "the listed part is not its own neighbour")


def broken() -> None:
    add_job("r-junk-1", brand="Mazda", model_key="CX-5", year=2019, vehicle=["not", "a", "dict"], diagrams=[
        ("J1", "BUMPERS", "Rear Bumper", ["FACE, RR. BUMPER", "BEAM COMP., RR. BUMPER UPPER"])])
    got = catalogue.diagram_neighbours("Mazda", "CX-5", 2019, ["Rear Bumper"])
    check(isinstance(got, dict), "a job whose vehicle is not a dict doesn't raise", str(got))
    db = SessionLocal()
    try:
        db.add(CatalogueSuggestion(report_id="r-junk-1", job_id=db.query(CatalogueJob).filter_by(report_id="r-junk-1").one().id,
                                   source_part="No Such Part", diagram_key="J2", diagram_name=None, name=None))
        db.add(CatalogueSuggestion(report_id="r-junk-1", job_id=db.query(CatalogueJob).filter_by(report_id="r-junk-1").one().id,
                                   source_part="Rear Bumper", diagram_key="J3", diagram_name="X", name="\x00�(((**", side="sideways"))
        db.commit()
    finally:
        db.close()
    check(isinstance(catalogue.diagram_neighbours("Mazda", "CX-5", 2019, ["Rear Bumper"]), dict), "rows with no name or a junk one don't raise")
    check(catalogue.diagram_neighbours("Honda", "Civic", "2008", ["Rear Bumper"]) == catalogue.diagram_neighbours("Honda", "Civic", None, ["Rear Bumper"]),
          "a year that is not a number is no year")
    check(catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper", None, 7, "Not A Part", "Rear Bumper"])
          == catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper"]), "junk in the list is skipped, a repeat counted once")
    check(catalogue.diagram_neighbours("Honda", "Civic", 2008, None) == {}, "parts=None: {}")

    class Boom:
        def __call__(self):
            raise RuntimeError("database is down")

    real, records = catalogue.SessionLocal, []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Grab()
    catalogue.log.addHandler(handler)
    catalogue.SessionLocal = Boom()
    try:
        got = catalogue.diagram_neighbours("Honda", "Civic", 2008, ["Rear Bumper"])
    finally:
        catalogue.SessionLocal = real
        catalogue.log.removeHandler(handler)
    check(got == {}, "the database failing: {}")
    check(any("neighbours" in r.getMessage() for r in records), "and it is logged")


def offline() -> None:
    check(CONNECTIONS == [], "no socket or httpx connection was attempted", str(CONNECTIONS))
    check(socket.socket.connect is _refuse, "the guard was in place throughout")


def main() -> int:
    for name, fn in (
        ("setup", setup), ("a cold cache", cold), ("warm the cache", warm), ("the car by year, and the default car", years),
        ("HR-V is HRV", hr_v), ("front and rear", end_filter), ("sides", sides), ("once each, at most 8", dedupe_and_cap),
        ("broken data never raises", broken), ("no network", offline),
    ):
        run_section(name, fn)
    print(f"\ntest_diagram_neighbours: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    if not FAILURES:
        print("\nALL DIAGRAM NEIGHBOUR CHECKS PASSED")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())

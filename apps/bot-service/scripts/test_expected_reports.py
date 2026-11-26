"""scripts/expected_reports.py, the private expected values of the
example-mode report tests: where it looks for expected-report-tests.json,
how a workbook's values and its labour lines are read, and that nothing
is found (no checks, no error) when there is no such file.

    python scripts/test_expected_reports.py

Offline; a scratch folder of made-up files only.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import expected_reports as er  # noqa: E402

PASSED = [0]
FAILURES: list[str] = []


def check(cond, label: str, why="") -> None:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append(label)
        print(f"    FAIL {label}" + (f"\n         {why}" if why != "" else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="expected-reports-test-"))
    folder = tmp / "cases" / "one"
    folder.mkdir(parents=True)
    workbook = folder / "CL 10001 TST 1001 A.xls"
    workbook.write_bytes(b"")
    values = {
        workbook.name: {"lump_sum": 1000.0, "repair_days": 3, "annex_b_lines": 2,
                        "labour": ["To test one", {"description": "To test two", "adjusted_cost": 80.0}]},
        "_labour_suggestions": {"lines": 9},
    }

    check(er.load(None) == {} and er.load(workbook) == {} and er.load(folder) == {},
          "no file anywhere: nothing expected (the tests make only the checks that need no real value)")
    check("only the checks that need no real value" in er.say({}), "and the run says so")

    bench = tmp / "cases" / "_benchmark"
    bench.mkdir()
    (bench / er.NAME).write_text(json.dumps(values), encoding="utf-8")
    check(er.load(workbook) == values and er.load(folder) == values and er.load(tmp / "cases") == values,
          "found in a _benchmark folder up the tree, from a workbook or a folder")
    (folder / er.NAME).write_text(json.dumps({workbook.name: {"repair_days": 9}}), encoding="utf-8")
    check(er.load(workbook) == {workbook.name: {"repair_days": 9}}, "the nearest file wins (beside the workbook)")
    (folder / er.NAME).unlink()

    want = er.for_workbook(er.load(workbook), workbook)
    check(want["lump_sum"] == 1000.0 and er.for_workbook(values, folder / "other.xls") == {},
          "a workbook's values by its file name; another workbook has none")
    check(er.labour_lines(want) == [{"description": "To test one"}, {"description": "To test two", "adjusted_cost": 80.0}],
          "labour lines: wording alone, or with figures")
    check("held against the private" in er.say(want), "the run says it held them against the file")

    (bench / er.NAME).write_text("[1, 2]", encoding="utf-8")
    try:
        er.load(workbook)
        check(False, "a file that isn't a JSON object is refused")
    except ValueError:
        check(True, "a file that isn't a JSON object is refused")

    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

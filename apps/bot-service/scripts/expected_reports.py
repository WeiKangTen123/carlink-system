"""The private expected values of the example-mode report tests
(test_report_render, test_parts_history, test_annexes). Not a test itself.

The firm's real workbooks stay in the private example_case folder, and so
do the values the tests hold them against: no real case's values are
written in git. When a test is run by hand with the private folder (or one
of its workbooks), it looks for `expected-report-tests.json` in the
workbook's folder, in a `_benchmark/` folder beside it, or in the same two
places up to three folders higher (e.g. example_case/_benchmark/
expected-report-tests.json). Found, the checks it lists are made; not
found, the tests make only the checks that need no real value (each file
against itself), and say so.

The file (JSON; every key optional -- a test checks the ones there):

    {
      "<workbook file name, e.g. CL 1xxxx ....xls>": {
        "make": "<make>", "model": "<model>",   # as history.parse_report reads them
        "date_of_survey": "...",                  # the Cover Page's "Date of survey", as written
        "lump_sum": 1234.0,                       # Annex C: the agreed contract lump sum, S$
        "repair_days": 7,                         # Annex C: the working days
        "annex_a_lines": 12,                      # Annex A part lines (List + S/Nett)
        "annex_b_lines": 3,                       # Annex B labour lines
        "labour": [                               # Annex B, in order: a line is its wording,
          "To ...",                               #   or {"description", "workshop_est",
          {"description": "To ...", "workshop_est": 100.0, "adjusted_cost": 80.0}
        ]                                         #   "adjusted_cost"} to check its figures too
      },
      "_labour_suggestions": {                    # test_annexes' labour suggestions over the
        "make": "<make>", "model": "<model>",    #   imported workbooks, for this make and model
        "lines": 9,                               #   lines offered in the first group
        "groups": 2,                              #   groups offered
        "cases_used": 2,                          #   cases they were drawn from
        "line": {"description": "To ...", "cases": 1,      # one line's figures
                 "usual_price": 100.0, "usual_workshop_est": 120.0},
        "other_group": {"descriptions": ["..."], "source": "1 past <make> <model> case"}
      }
    }
"""
import json
from pathlib import Path

NAME = "expected-report-tests.json"


def load(start: Path | None) -> dict:
    """The expected values for the workbooks at or under `start` (a
    workbook or a folder), or {} when there is no such file."""
    if start is None:
        return {}
    base = start if start.is_dir() else start.parent
    for folder in (base, *list(base.parents)[:3]):
        for path in (folder / NAME, folder / "_benchmark" / NAME):
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError(f"{path}: not a JSON object")
                return data
    return {}


def for_workbook(expected: dict, path: Path) -> dict:
    """One workbook's expected values ({} when none)."""
    found = expected.get(path.name)
    return found if isinstance(found, dict) else {}


def labour_lines(expected: dict) -> list[dict]:
    """A workbook's expected "labour", each line as {"description", and
    "workshop_est" / "adjusted_cost" when given}."""
    out = []
    for line in expected.get("labour") or []:
        out.append({"description": line} if isinstance(line, str) else dict(line))
    return out


def say(expected: dict) -> str:
    """The run's line about which checks it makes."""
    if expected:
        return f"held against the private {NAME}"
    return f"no {NAME} beside the workbooks: only the checks that need no real value"

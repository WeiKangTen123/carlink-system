"""Annex A line ids (Stage 1 review H2, 24 Nov 2026).

Every Annex A line carries a `line_id` the server gives it
(schema.AnnexAPartItem.line_id). A save matches each line a page sends to
the line it was by that id -- never by its wording, which two lines can
share ("Bracket" twice, or the two "Tail lamp" rows the sync keeps while
the sides are unconfirmed) and which a rename changes. Matching by wording
let an ordinary save compare the second "Bracket" with the first, call its
untouched book price "typed", confirm it and remember it as the person's
own.

An id is made once and then kept:
  - a line written without one (a new line, a case from before ids) gets
    one when its Annex A is next written (ensure_line_ids, from
    synthesize_annexes_for_report and the routes that save lines);
  - a line read without one gets the SAME id on the way out (GET /reports/
    {id}, with_line_ids) as the next write will give it: the id is worked
    out from the line's place in the stored list and its wording, so a page
    opened before the case's first write sends back ids the save knows.
Unique within a case only; never shown, never printed.

No imports from the rest of the app: synthesize_annexes, the API routes
and prefill all use this.
"""
import hashlib
import re
from typing import Callable

# What an id may look like. Anything else a page sends is no id at all.
_VALID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_VALID.match(value))


def _made_id(index: int, wording, attempt: int) -> str:
    """The id a line at `index` with this wording gets: the same every
    time (load and save agree), a different one per attempt for a clash."""
    text = f"{index}|{' '.join(str(wording or '').lower().split())}|{attempt}"
    return "L" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def ensure_line_ids(items: list) -> list:
    """In place, and returned: every line (a dict with a part name or not)
    without a valid line_id -- or with one an earlier line of the list
    already has -- gets one made from its place and wording (_made_id),
    clear of every id in the list."""
    # The place among the lines (dicts) only, so a list read with anything
    # else in it numbers its lines as one without does.
    lines = [it for it in items if isinstance(it, dict)]
    taken = {it.get("line_id") for it in lines if valid_id(it.get("line_id"))}
    seen: set[str] = set()
    for index, it in enumerate(lines):
        lid = it.get("line_id")
        if valid_id(lid) and lid not in seen:
            seen.add(lid)
            continue
        attempt = 0
        new = _made_id(index, it.get("part_name"), attempt)
        while new in taken:
            attempt += 1
            new = _made_id(index, it.get("part_name"), attempt)
        it["line_id"] = new
        taken.add(new)
        seen.add(new)
    return items


def with_line_ids(data: dict | None) -> dict | None:
    """The case data as GET /reports/{id} answers it: a line stored without
    an id gets the one its next write will give it (ensure_line_ids over
    the stored list), on copies -- nothing is written. The data itself when
    every line has one."""
    if not isinstance(data, dict) or not isinstance(data.get("annex_a"), dict):
        return data
    items = data["annex_a"].get("items")
    if not isinstance(items, list) or all(not isinstance(it, dict) or valid_id(it.get("line_id")) for it in items):
        return data
    copies = ensure_line_ids([dict(it) if isinstance(it, dict) else it for it in items])
    return {**data, "annex_a": {**data["annex_a"], "items": copies}}


def pair_lines(stored: list[dict], sent: list[dict], key: Callable[[str], object]) -> list[dict | None]:
    """For each line a page sent, the stored line it is, or None for a new
    line. `stored` must carry their ids (ensure_line_ids). First by
    line_id: each stored line answers one sent line at most. A sent line
    without an id the stored list has (an older page, an API client, a
    line removed meanwhile) is paired by its wording (`key` of part_name)
    with the stored lines nobody claimed, IN ORDER: the second sent
    "Bracket" with the second stored "Bracket", never the first again."""
    by_id: dict[str, dict] = {}
    for s in stored:
        lid = s.get("line_id")
        if valid_id(lid) and lid not in by_id:
            by_id[lid] = s
    used: set[int] = set()
    out: list[dict | None] = [None] * len(sent)
    pending: list[int] = []
    for i, row in enumerate(sent):
        lid = row.get("line_id")
        match = by_id.get(lid) if valid_id(lid) else None
        if match is not None and id(match) not in used:
            out[i] = match
            used.add(id(match))
        else:
            pending.append(i)
    if pending:
        queues: dict[object, list[dict]] = {}
        for s in stored:
            if id(s) not in used:
                queues.setdefault(key(str(s.get("part_name") or "")), []).append(s)
        for i in pending:
            queue = queues.get(key(str(sent[i].get("part_name") or "")))
            if queue:
                match = queue.pop(0)
                out[i] = match
                used.add(id(match))
    return out

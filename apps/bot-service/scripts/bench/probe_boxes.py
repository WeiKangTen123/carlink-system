"""Draws N random evidence boxes from a benchmark result onto their photos,
for a person to judge -- contract 9 run R3: on target in at least 14 of 20,
the AI's boxes are shown (intake_zone_evidence "reason_box"); otherwise the
reasons without boxes ("reason").

    python scripts/bench/probe_boxes.py RESULT.json --cases DIR --key key.json --out-dir DIR
        [--n 20] [--seed 1] [--from all|suggestions|list] [--kind ai|sort|any]

Offline: no Gemini call, no database, the app is not imported. From every
scored run of the result it takes each suggestion's and each list item's
evidence entry with a box -- the AI's by default (--kind) -- and, for a list
item with no evidence, its bbox_2d on its photo_reference; picks N at
random (--seed: the same pick again); and for each writes the photo upright
(EXIF), at most 1600 px, the box in red, under a caption band (the part,
which read drew it, the AI's reason), to OUT-DIR/box01.jpg, box02.jpg...
with

    index.csv    one row per image: file, case no., run, photo, part, where
                 (suggestion / list), by, box kind, and an empty
                 "on_target" column for the person to fill (y / n)
    index.json   the same with the box, the reason and the frame check

A box is on the frame the AI was sent (the upright 1600 px preview), 0-1000
both ways, so it maps onto the upright photo at any size; when the
evidence says which frame (img_w / img_h) and its shape differs from the
photo's by more than 2%, the row says so ("frame differs": the app hides
such a box).

The photos are real: OUT-DIR must be outside the repository, with the
benchmark files. File names carry no case name or plate (cases are
numbered as in the key).

On the server, inside the container that ran the benchmark (the run's code
is at /tmp/bench/code/LABEL), then copied out:

    docker exec -w /tmp/bench/code/LABEL/apps/bot-service carlink_api python scripts/bench/probe_boxes.py \\
        /tmp/bench/out/LABEL.json --cases /tmp/bench/cases --key /tmp/bench/key.json --n 20 --out-dir /tmp/bench/probe-LABEL
    docker cp carlink_api:/tmp/bench/probe-LABEL ~/carlink-bench/probe-LABEL
"""
import argparse
import csv
import json
import random
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = SCRIPTS_DIR.parent
REPO_DIR = BOT_DIR.parents[1]
for _path in (str(BOT_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# benchmark_intake imports nothing of the app when it loads.
from benchmark_intake import IMAGE_EXTENSIONS, _positive, run_ok  # noqa: E402

MAX_SIDE = 1600
# A box drawn on a frame whose shape differs by more than this from the
# photo's (PhotoBoxes' rule, contract 9 section 7.2).
FRAME_TOLERANCE = 0.02


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="probe_boxes.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("result", type=Path, help="a benchmark result file (benchmark_intake.py --out)")
    ap.add_argument("--cases", type=Path, required=True, help="the cases' photo folders (only read)")
    ap.add_argument("--key", type=Path, required=True, help="the answer key: each case's folder")
    ap.add_argument("--out-dir", type=Path, required=True, help="output folder, outside the repository (real photos)")
    ap.add_argument("--n", type=_positive, default=20, help="boxes to draw (default 20)")
    ap.add_argument("--seed", type=int, default=1, help="the random pick (default 1)")
    ap.add_argument("--from", dest="source", choices=("all", "suggestions", "list"), default="all",
                    help="the suggestions' boxes, the list's, or both (default)")
    ap.add_argument("--kind", choices=("ai", "sort", "any"), default="ai",
                    help="the AI's boxes (default), the sorter's, or either")
    return ap.parse_args(argv)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _box(value) -> list[int] | None:
    """Four numbers on 0-1000 the right way round, else None: a stored box
    is cleaned already, but an old file's may not be."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in value):
        return None
    ymin, xmin, ymax, xmax = value
    return [int(v) for v in value] if ymin < ymax and xmin < xmax else None


def candidates(doc: dict, case_no: dict[str, int], source: str = "all", kind: str = "ai") -> list[dict]:
    """Every box the result holds that may be drawn: (run, where, part,
    photo label, box, by, kind, reason, frame)."""
    out = []
    for run in doc.get("runs") or []:
        if not run_ok(run) or run.get("case") not in case_no:
            continue
        groups = []
        if source in ("all", "suggestions"):
            groups.append(("suggestion", run.get("suggestions") or []))
        if source in ("all", "list"):
            groups.append(("list", run.get("drafted_items") or []))
        for where, entries in groups:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                evidence = [e for e in entry.get("evidence") or [] if isinstance(e, dict)]
                if not evidence and where == "list" and entry.get("bbox_2d") and entry.get("photo_reference"):
                    # An item from before the evidence: its own box.
                    evidence = [{"photo": entry["photo_reference"], "box": entry["bbox_2d"], "box_kind": "ai",
                                 "by": "overall", "reason": entry.get("reason")}]
                for e in evidence:
                    box = _box(e.get("box"))
                    box_kind = e.get("box_kind") or "ai"
                    if box is None or (kind != "any" and box_kind != kind):
                        continue
                    if not str(e.get("photo") or "").startswith("P"):
                        continue  # a photo deleted before the run ended: nothing to draw on
                    out.append({
                        "case": run["case"], "case_no": case_no[run["case"]], "rep": int(run.get("rep") or 1),
                        "where": where, "part": entry.get("part"), "name": entry.get("other_part_name") or entry.get("name"),
                        "photo": e["photo"], "box": box, "by": e.get("by"), "box_kind": box_kind,
                        "reason": e.get("reason") or entry.get("reason"), "frame": [e.get("img_w"), e.get("img_h")],
                        "_files": [p.get("file") for p in run.get("sorted_photos") or []],
                    })
    return out


def photo_file(folder: Path, files: list, label: str) -> Path | None:
    """The photo a label names: the run's sorted_photos are in the case
    folder's camera order (benchmark_intake.load_case)."""
    try:
        name = files[int(label[1:]) - 1]
    except (ValueError, IndexError, TypeError):
        return None
    found = sorted(p for p in folder.rglob("*") if p.name == name and p.suffix.lower() in IMAGE_EXTENSIONS)
    return found[0] if found else None


def frame_differs(frame, size) -> bool:
    w, h = frame if isinstance(frame, (list, tuple)) and len(frame) == 2 else (None, None)
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        return False
    return abs((w / h) / (size[0] / size[1]) - 1) > FRAME_TOLERANCE


def draw(src: Path, pick: dict, dest: Path) -> tuple[int, int]:
    """The photo upright, at most MAX_SIDE, with the box, under a caption
    band (so the caption covers none of the photo). Returns the photo's
    size."""
    from PIL import Image, ImageDraw, ImageOps

    with Image.open(src) as opened:
        photo = ImageOps.exif_transpose(opened).convert("RGB")
    photo.thumbnail((MAX_SIDE, MAX_SIDE))
    w, h = photo.size
    caption = [f"{pick['part']}" + (f" ({pick['name']})" if pick.get("name") else ""),
               f"{pick['where']} | by {pick['by']} | {pick['box_kind']} box | {pick['photo']}"]
    if pick.get("reason"):
        caption.append(f"AI says: {pick['reason']}")
    band = 14 * len(caption) + 8
    image = Image.new("RGB", (w, h + band), (0, 0, 0))
    image.paste(photo, (0, band))
    pen = ImageDraw.Draw(image)
    for i, text in enumerate(caption):
        pen.text((6, 4 + 14 * i), text[:160], fill=(255, 255, 255))
    width = max(3, round(min(w, h) / 150))
    ymin, xmin, ymax, xmax = pick["box"]
    pen.rectangle((xmin * w / 1000, band + ymin * h / 1000, xmax * w / 1000, band + ymax * h / 1000),
                  outline=(255, 0, 0), width=width)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=85)
    return w, h


def main(argv=None) -> int:
    args = parse_args(argv)
    if _inside(args.out_dir, REPO_DIR):
        sys.exit("--out-dir: inside the repository; the probe writes real photos -- put it with the benchmark files")
    doc = json.loads(args.result.read_text(encoding="utf-8"))
    cases = json.loads(args.key.read_text(encoding="utf-8"))["cases"]
    case_no = {c["name"]: i + 1 for i, c in enumerate(cases)}
    folders = {c["name"]: args.cases / c["folder"] for c in cases}
    pool = candidates(doc, case_no, args.source, args.kind)
    if not pool:
        sys.exit("no box in the result to draw (a run without --zone-evidence reason_box keeps few or none)")
    picked = random.Random(args.seed).sample(pool, min(args.n, len(pool)))
    shutil.rmtree(args.out_dir, ignore_errors=True)
    args.out_dir.mkdir(parents=True)
    rows = []
    for n, pick in enumerate(picked, start=1):
        src = photo_file(folders[pick["case"]], pick.pop("_files"), pick["photo"])
        row = {k: v for k, v in pick.items() if k != "case"}
        row["file"] = f"box{n:02d}.jpg"
        if src is None:
            row["file"], row["problem"] = None, "photo not found in the case folder"
        else:
            size = draw(src, pick, args.out_dir / row["file"])
            row["frame_differs"] = frame_differs(pick.get("frame"), size)
        rows.append(row)
    (args.out_dir / "index.json").write_text(json.dumps({"result": str(args.result), "label": doc.get("label"),
                                                         "seed": args.seed, "from": args.source, "kind": args.kind,
                                                         "pool": len(pool), "boxes": rows}, indent=1), encoding="utf-8")
    with open(args.out_dir / "index.csv", "w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["file", "case_no", "run", "photo", "part", "where", "by", "box_kind", "frame_differs", "on_target"])
        for row in rows:
            out.writerow([row["file"] or "-", row["case_no"], row["rep"], row["photo"], row["part"], row["where"],
                          row["by"], row["box_kind"], "yes" if row.get("frame_differs") else "", ""])
    drawn = sum(1 for r in rows if r["file"])
    print(f"{drawn} of {len(pool)} box(es) drawn into {args.out_dir} (seed {args.seed}); "
          f"mark on_target in index.csv: R3 passes at 14 of 20")
    return 0 if drawn else 1


if __name__ == "__main__":
    raise SystemExit(main())

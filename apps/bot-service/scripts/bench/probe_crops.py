"""LIVE probe of the sorter's damage boxes (contract 8 D1), before a
benchmark is spent on close-ups.

The reading's close-ups (settings.intake_read_crops) are cut from the boxes
the sorter draws round the damage (settings.intake_sort_damage_box), and the
sort runs on the light models. Whether their boxes land on the damage at
all is the first thing to know. This runs ONLY the sorter, with the box on,
on N photos of one case folder (default 12: one sorting call), through the
real call_structured, and writes what came back to an output folder:

    boxes.json        per photo: kind, area, part, note, the box, and the
                      close-up's file (or why there is none)
    crops/P03.jpg     each close-up, cut exactly as the reading would cut it
                      (intake/crops.make_crop)
    boxed/P03.jpg     the photo with the sorter's box (red) and the
                      close-up's window (yellow), to judge a box at a glance

The photos go through what an upload does (a copy and its 1600 px preview,
which is what the sorter is sent), in a scratch STORAGE_DIR; the case
folder is only read. The output holds real photos: keep it with the other
benchmark files (~/carlink-bench, /tmp/bench), never in git -- an --out
inside the repository is refused.

Like benchmark_intake.py it needs a scratch database and storage
(BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR, or --scratch DIR):
the app binds to DATABASE_URL on import. The key is the company key the
environment gives (GEMINI_API_KEY); it is never printed, and errors are
scrubbed (runs.scrub_error). Gemini calls: one per 12 photos, plus a
fallback model's try when one fails.

    BENCHMARK_SCRATCH=1 DATABASE_URL=sqlite:////tmp/bench/probe/run.db STORAGE_DIR=/tmp/bench/probe/storage \\
        python scripts/bench/probe_crops.py --case /tmp/bench/cases/CASE --out /tmp/bench/probe/out \\
        [--photos 12] [--triage-models gemini-3.5-flash-lite,gemini-3.1-flash-lite] [--thinking-level-sort L]
"""
import argparse
import json
import os
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
from benchmark_intake import IMAGE_EXTENSIONS, REFUSAL, _chain, _positive, _use_scratch  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="probe_crops.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--case", type=Path, required=True, help="one case's photo folder (only read)")
    ap.add_argument("--out", type=Path, required=True, help="output folder, outside the repository (real photos)")
    ap.add_argument("--photos", type=_positive, default=12, metavar="N",
                    help="photos sorted, spread over the case's camera order (default 12: one sorting call)")
    ap.add_argument("--triage-models", type=_chain, metavar="CHAIN", help="the sorting chain (default the setting's)")
    ap.add_argument("--thinking-level-sort", metavar="LEVEL", help="settings.gemini_thinking_level_sort for the probe")
    ap.add_argument("--scratch", type=Path, metavar="DIR",
                    help="a throwaway folder for the database and storage (stands for BENCHMARK_SCRATCH=1)")
    return ap.parse_args(argv)


def spread(files: list[Path], n: int) -> list[Path]:
    """n of the files, evenly over the camera order (first and last
    included): a probe of the first 12 alone would often be all overviews."""
    if n >= len(files):
        return list(files)
    if n == 1:
        return [files[0]]
    picked = sorted({round(i * (len(files) - 1) / (n - 1)) for i in range(n)})
    return [files[i] for i in picked]


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _outline(preview: str, box: list[int], dest: Path, crop_window) -> None:
    """The preview with the box (red) and the close-up's window (yellow)."""
    from PIL import Image, ImageDraw

    with Image.open(preview) as opened:
        image = opened.convert("RGB")
    w, h = image.size
    draw = ImageDraw.Draw(image)
    width = max(2, round(min(w, h) / 200))
    ymin, xmin, ymax, xmax = box
    draw.rectangle((xmin * w / 1000, ymin * h / 1000, xmax * w / 1000, ymax * h / 1000), outline=(255, 0, 0), width=width)
    window = crop_window(box, w, h)
    if window:
        draw.rectangle(window, outline=(255, 220, 0), width=width)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=85)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.scratch:
        _use_scratch(args.scratch)
    if os.environ.get("BENCHMARK_SCRATCH") != "1" or not os.environ.get("DATABASE_URL") or not os.environ.get("STORAGE_DIR"):
        sys.exit(REFUSAL)
    if not args.case.is_dir():
        sys.exit(f"--case: not a folder: {args.case}")
    if _inside(args.out, REPO_DIR):
        sys.exit("--out: inside the repository; the probe writes real photos -- put it with the benchmark files")
    files = sorted((p for p in args.case.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name.lower())
    if not files:
        sys.exit(f"--case: no photos in {args.case}")

    from app import config
    from app.ai import runs, triage
    from app.ai.keys import AiActor
    from app.config import settings
    from app.intake import crops
    from app.intake.photos import ai_input_path
    from app.storage.files import generate_preview

    settings.intake_sort_damage_box = True
    if args.triage_models:
        settings.gemini_triage_model_chain = args.triage_models
    if args.thinking_level_sort is not None:
        try:
            settings.gemini_thinking_level_sort = config.normal_thinking_levels(args.thinking_level_sort)
        except ValueError as exc:
            sys.exit(f"--thinking-level-sort: {exc}")

    work = Path(settings.storage_dir) / "probe"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    photos = []
    for n, src in enumerate(spread(files, args.photos), start=1):
        copy = work / f"{n:03d}{src.suffix.lower()}"
        shutil.copyfile(src, copy)
        generate_preview(str(copy))
        photos.append({"label": f"P{n:02d}", "file": src.name, "path": str(copy), "ai_path": ai_input_path(str(copy))})

    out = args.out
    shutil.rmtree(out / "crops", ignore_errors=True)
    shutil.rmtree(out / "boxed", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Sorting {len(photos)} of {len(files)} photos with the damage box on "
          f"{settings.gemini_triage_model_chain} (prompt {triage.sort_prompt_version()})", flush=True)

    found: dict[str, dict] = {}
    tries: list[dict] = []
    errors: list[str] = []
    calls = 0
    for start in range(0, len(photos), triage.BATCH_SIZE):
        batch = photos[start:start + triage.BATCH_SIZE]
        calls += 1
        with runs.collecting() as attempts:
            try:
                found.update(triage.triage_photos([(p["label"], p["ai_path"]) for p in batch], actor=AiActor.cli()))
            except Exception as exc:
                errors.append(runs.scrub_error(f"{type(exc).__name__}: {exc}") or type(exc).__name__)
            done, _ = attempts.drain()
        # Model, outcome, time and tokens only: never the key's last 4.
        tries += [{k: a.get(k) for k in ("model", "outcome", "ms", "prompt_tokens", "output_tokens") if k in a}
                  for a in done]

    rows = []
    for p in photos:
        got = found.get(p["label"])
        row = {"label": p["label"], "file": p["file"]}
        if got is None:
            row["result"] = "not sorted"
            rows.append(row)
            continue
        row.update({k: got.get(k) for k in ("kind", "area", "quality", "shows_damage", "part", "note", "damage_box")})
        box = got.get("damage_box")
        if box:
            crop = crops.make_crop([p["path"], p["ai_path"]], box)
            if crop:
                dest = out / "crops" / f"{p['label']}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(crop, dest)
                row["crop"] = f"crops/{dest.name}"
            else:
                row["crop"] = None
                row["why_no_crop"] = f"the close-up would cover over {crops.MAX_AREA_SHARE:.0%} of the photo, or it didn't open"
            try:
                _outline(p["ai_path"], box, out / "boxed" / f"{p['label']}.jpg", crops.crop_window)
                row["boxed"] = f"boxed/{p['label']}.jpg"
            except Exception as exc:
                row["boxed"] = None
                errors.append(f"outline {p['label']}: {type(exc).__name__}")
        elif got.get("shows_damage"):
            row["why_no_crop"] = "shows damage but no usable box (missing, out of 0-1000, inverted or empty)"
        rows.append(row)

    damage = [r for r in rows if r.get("shows_damage")]
    boxed = [r for r in rows if r.get("damage_box")]
    cut = [r for r in rows if r.get("crop")]
    summary = {
        "photos": len(photos), "sorted": len(found), "shows_damage": len(damage), "boxed": len(boxed),
        "close_ups": len(cut), "sort_calls": calls, "tries": tries, "errors": errors,
        "triage_model_chain": settings.gemini_triage_model_chain, "sort_prompt_version": triage.sort_prompt_version(),
        "crop_rule": {"grow": crops.GROW, "min_share": crops.MIN_SHARE, "max_area_share": crops.MAX_AREA_SHARE,
                      "max_edge": crops.MAX_EDGE},
    }
    (out / "boxes.json").write_text(json.dumps({"summary": summary, "photos": rows}, indent=1), encoding="utf-8")
    for r in rows:
        print(f"  {r['label']} {r.get('kind') or r.get('result', '-'):<11} damage={str(bool(r.get('shows_damage'))):<5} "
              f"box={r.get('damage_box')} -> {r.get('crop') or r.get('why_no_crop') or '-'}", flush=True)
    print(f"{len(found)}/{len(photos)} sorted, {len(damage)} showing damage, {len(boxed)} boxed, {len(cut)} close-ups; "
          f"{calls} sorting call(s), {len(tries)} tr{'y' if len(tries) == 1 else 'ies'}: "
          f"{[(t.get('model'), t.get('outcome')) for t in tries]}", flush=True)
    for e in errors:
        print(f"  error: {e}", flush=True)
    print(f"written {out / 'boxes.json'}", flush=True)
    return 1 if errors and not found else 0


if __name__ == "__main__":
    raise SystemExit(main())

# Intake benchmark: running it on the server

`scripts/benchmark_intake.py` runs the real intake pipeline (sorting, then reading, with real
Gemini calls) on the example cases and scores the result against what the surveyors wrote. This
folder holds what runs it on the server and compares two runs:

| file | what it does |
|---|---|
| `run_bench.sh` | on the server, as user `weika`: runs the benchmark for one git ref inside the live `carlink_api` container, on a scratch database, and copies the result out |
| `compare.py` | before/after table of two result files, with ranges and a noise verdict |
| `../test_benchmark_scoring.py` | offline tests of the scoring (synthetic data, no Gemini) |

## Rules

- **The Gemini key is never handled.** The live container already has it in its environment and
  `docker exec` inherits it. Never `docker inspect`, `printenv` or copy it anywhere.
- **The live quota is shared, not bypassed.** The benchmark's `STORAGE_DIR/.ratelimit` is a
  symlink to the live `/app/storage/.ratelimit`, so its calls count in the same 12-per-minute
  window as the app's (`app/ai/rate_limit.py`). A run slows the app's own AI use a little: run it
  outside working hours.
- **Nothing live is written.** The code comes from `git archive` (the checkout in
  `/opt/carlink-system` is never switched); the database and storage are scratch folders under
  `/tmp/bench/LABEL` in the container.
- **PII stays out of git and backups.** The photos, `key.json`, the report spreadsheets, the
  results and logs hold real plates and chassis numbers. They live only in `~/carlink-bench`
  (mode 0700) and in the container's `/tmp/bench` (0700).

## One-time setup

`~/carlink-bench` on the server:

```
~/carlink-bench/
  cases/<folder>/...   1600 px copies of each case's photos; <folder> = the case's "folder" in key.json
  key.json             the answer key (example_case/_benchmark/key.json on the owner's PC)
  reports/             the CL report spreadsheets (any layout; matched to a case by plate, else by folder name)
  run_bench.sh         this script (see "Get the runner")
  code/ out/ *.log     written by run_bench.sh
```

`cases/` and `key.json` have been there since the 2026-11-05 run. Copy the reports up from the
PC once (the names have spaces: quote them), then `chmod 600 ~/carlink-bench/reports/*`.

Copy the inputs into the container. A deploy that recreates `carlink_api` empties its `/tmp`, so
this must be done again after a deploy. `run_bench.sh` checks for the inputs and prints these
lines if any is missing:

```
docker exec carlink_api sh -c 'mkdir -p /tmp/bench && chmod 700 /tmp/bench'
docker cp ~/carlink-bench/cases    carlink_api:/tmp/bench/cases
docker cp ~/carlink-bench/key.json carlink_api:/tmp/bench/key.json
docker cp ~/carlink-bench/reports  carlink_api:/tmp/bench/reports
```

## Get the runner

```
git -C /opt/carlink-system fetch origin feat/bench-harness     # master once merged
git -C /opt/carlink-system show FETCH_HEAD:apps/bot-service/scripts/bench/run_bench.sh > ~/carlink-bench/run_bench.sh
chmod 700 ~/carlink-bench/run_bench.sh
```

## Run

```
tmux new -s bench          # an SSH drop would kill the copy-out step (the run itself carries on in the container)
bash ~/carlink-bench/run_bench.sh LABEL GIT_REF [benchmark_intake.py options]
```

- `LABEL`: the run's name (letters, digits, `.` `_` `-`; not `cases`, `reports`, `out`...). It
  names `~/carlink-bench/out/LABEL.json` and `LABEL.log`. Reusing a label replaces its scratch
  folder and result.
- `GIT_REF`: a branch, tag or commit on `origin`. The result records it with the commit
  (`--ref "GIT_REF@sha"`).
- Options go on to the benchmark: `--repeat N`, `--only "<case name>"` (repeatable),
  `--models CHAIN`, `--triage-models CHAIN`, `--keep-photos`, and the model settings
  (below): `--thinking-level-read`, `--thinking-level-sort`, `--thinking-level-second-look`,
  `--image-resolution`, `--read-tools`.

Examples:

```
# baseline: master's pipeline (this branch is master + the harness), 3 runs per case
bash ~/carlink-bench/run_bench.sh baseline-3x feat/bench-harness --repeat 3

# a branch that doesn't carry the harness: overlay it from feat/bench-harness
BENCH_HARNESS_REF=feat/bench-harness bash ~/carlink-bench/run_bench.sh a1-3x feat/detection-wording --repeat 3

# reading model A/B on the same frozen sort
bash ~/carlink-bench/run_bench.sh read-36 feat/bench-harness --repeat 3 --models gemini-3.6-flash

# the lite reader thinking more, its close-ups at 2240 tokens
bash ~/carlink-bench/run_bench.sh lite-medium master --repeat 3 --models gemini-3.5-flash-lite \
    --thinking-level-read medium --image-resolution 'damage=ultra_high,tyre=ultra_high,overview=medium'
```

Environment overrides: `BENCH_HARNESS_REF` (above), `BENCH_NO_SORT_CACHE=1` (sort afresh every
run, to measure sorting itself), `BENCH_REPO` (`/opt/carlink-system`), `BENCH_CONTAINER`
(`carlink_api`), `BENCH_HOME` (`~/carlink-bench`), `BENCH_MIN_FREE_GB` (5).

What `run_bench.sh` does, in order:

1. Checks the label and ref, that it isn't root, that no other run is going (a lock: two runs
   would share one quota window), that `carlink_api` is running, that Docker's disk has 5 GB
   free (the app refuses uploads under 3 GB), and that the inputs are in the container.
2. `git fetch origin GIT_REF` and `git archive` of `apps/bot-service` into
   `~/carlink-bench/code/LABEL` (umask 077); with `BENCH_HARNESS_REF`, `benchmark_intake.py`
   and `scripts/bench/` from that ref on top.
3. Empties `/tmp/bench/LABEL` and `/tmp/bench/code/LABEL` in the container, `docker cp`s the
   code in, and links `/tmp/bench/LABEL/storage/.ratelimit` to `/app/storage/.ratelimit`.
4. `docker exec -e BENCHMARK_SCRATCH=1 -e DATABASE_URL=sqlite:////tmp/bench/LABEL/run.db
   -e STORAGE_DIR=/tmp/bench/LABEL/storage carlink_api python .../benchmark_intake.py
   --cases /tmp/bench/cases --key /tmp/bench/key.json --reports /tmp/bench/reports
   --sort-cache /tmp/bench/sortcache --out /tmp/bench/out/LABEL.json --label LABEL --ref ...`,
   the output also going to `~/carlink-bench/LABEL.log`. Ctrl-C stops the process inside the
   container too (`docker exec` alone would leave it running).
5. Copies `/tmp/bench/out/LABEL.json` to `~/carlink-bench/out/` (0600), even after a failed or
   stopped run: the benchmark rewrites the file after every case run.

Cost and time, from the 2026-11-05 numbers: one round of the four cases is about 26 Gemini calls
without the sort cache (22 sorting batches of 12 photos, 4 reading calls) and 5-6 minutes;
with the cache filled, 4 calls and about 2 minutes. `--repeat 3` from an empty cache: about 34
calls, 10 minutes, plus waits when the live app is busy.

## The model settings

`app/config.py` has one setting per kind of reading call, all unset by default. Unset, a
setting sends nothing: the request is byte for byte what it was before they existed, so a run
without these flags measures what production does today. Each flag sets one for the run, and
the result file keeps them under `ai_settings` (compare.py prints them beside each label).

| flag | setting | what it sends |
|---|---|---|
| `--thinking-level-read LEVEL` | `gemini_thinking_level_read` | `generation_config: {thinking_level}` on the reading call (and the Telegram bot's drafting, the same call) |
| `--thinking-level-sort LEVEL` | `gemini_thinking_level_sort` | the same on the sorting calls; a sort cache folder of its own |
| `--thinking-level-second-look LEVEL` | `gemini_thinking_level_second_look` | the same on the second look |
| `--image-resolution SPEC` | `gemini_image_resolution` | `"resolution"` on each image of the reading call, by the photo's kind |
| `--read-tools code_execution` | `gemini_read_tools` | `tools: [{type: code_execution}]` on the reading call (Agentic Vision trial) |

- LEVEL: `minimal`, `low`, `medium` or `high` for every model of the chain, or per model:
  `gemini-3.5-flash-lite=medium`, or `low,gemini-3-flash-preview=high` (the bare level for the
  models not named). A model given a level it refuses (gemini-3.8-flash has no `minimal`)
  fails that try with a 400 and the chain moves on, as on any other error. Defaults per
  Google: gemini-3.5-flash-lite `minimal`, gemini-3-flash-preview `high`.
- SPEC: `kind=resolution` pairs, `*` for every other kind, e.g.
  `damage=ultra_high,tyre=ultra_high,overview=medium`. Kinds are the sorter's
  (`app/ai/triage.PHOTO_KINDS`) plus `unsorted`; resolutions `low`, `medium`, `high`,
  `ultra_high` (280, 560, 1120, 2240 tokens a photo on Gemini 3; unset is Gemini's default,
  1120). It reaches the request only where the reading's image builder passes each photo's
  kind (`structured.image_part(path, kind=...)`); each call's `images` in the result shows it.
- `code_execution` with a JSON schema is a preview feature on Gemini 3 and Google documents
  image zoom/crop for Gemini 3 Flash only: try it on each model with `smoke_schemas.py --live
  --read-tools code_execution` first. A try that used it counts `code runs`.

Each run's `ai_calls` lists every Gemini call: purpose, the model that answered, seconds,
tries, prompt / output / thinking tokens (when Google reports them), code runs, and the
images it sent by resolution. The log prints one line per reading and second-look call.

## The fair score

The strict `parts` score is unchanged. Next to it, `parts_fair`:

- **neighbour, half a part**: a drafted part on the same side next to a key part the strict
  score missed -- front fender, front door, rear door, rear quarter in a row; the sill and
  either door; the wheel-arch liner or trim and the fender (front) or quarter (rear); the
  arch liner and the wheel at the same end; and, for the parts contract 6 splits out, the
  arch trim and the liner, the tyre and the wheel. Not an extra. One neighbour per key part.
- **Other named like a key part**: an "Other / Not Listed" item whose name
  (`other_part_name`) shares a word of five letters or more (not front, right, upper, lower,
  inner, outer, centre) with a key part's surveyor wording is not an extra.
- **contradicted** (with `--reports`): a drafted part whose every line in the held-out report
  (same family, side and end of the car) says Serviceable -- the surveyor looked and passed
  it. A flag; no number changes.

`fair_found`, `fair_visible_found`, `fair_extra`, `neighbour`, `other_named` and
`contradicted` are metrics like the others (summary, compare.py).

### Rescore a stored result

```
python scripts/benchmark_intake.py --rescore out/a1-3x.json --key key.json [--reports reports/] [--out a1-3x-rescored.json]
```

Scores every run of a result file again -- strict and fair -- from the drafted items it
kept, against the key (and each case's report). No Gemini call, no database, no
`BENCHMARK_SCRATCH`; the stored file is never written (`--out` must be another file). Runs
from before `drafted_items` existed are scored from `drafted_parts`; their Other items have no
name, so they stay extras. A run whose strict score comes out different was scored with an
older key: the console says how many.

## On a PC

The same script runs on Windows. Nothing is shared with the server: the rate-limit window is
the scratch folder's own, so with the company key the live app's per-minute quota is not
counted -- run outside working hours, or with your own key. PowerShell, from
`apps/bot-service`, with the venv:

```
$env:GEMINI_API_KEY = "<the key>"          # this process only; never in a file
$env:PYTHONIOENCODING = "utf-8"
$ex = "C:/Users/weika/OneDrive/Desktop/carlink-system-master/example_case"
python scripts/benchmark_intake.py --scratch C:/bench/lite-medium `
    --cases $ex --key $ex/_benchmark/key.json --reports $ex --sort-cache C:/bench/sortcache `
    --out C:/bench/out/lite-medium.json --repeat 3 --models gemini-3.5-flash-lite --thinking-level-read medium
```

- `--scratch DIR` puts the run's database (`DIR/run.db`) and storage in DIR and stands for
  `BENCHMARK_SCRATCH=1`; it refuses when the app is already loaded on another database.
- `BENCH_CASES`, `BENCH_KEY`, `BENCH_REPORTS`, `BENCH_SORT_CACHE` give the defaults of
  `--cases`, `--key`, `--reports`, `--sort-cache`.
- `example_case` holds each case's photo folder under the key's `folder` names, and their
  CL reports beside the photos: `--reports` finds them by plate. The photos there are the
  camera originals; the app reads the 1600 px previews it makes of them, as on the server.
- The sort cache starts empty on a PC: the first run sorts every photo (about 22 calls).

## The sort cache

`/tmp/bench/sortcache` keeps each photo's sorting result by its sha256, in a folder per sorting
setup: the triage model chain, `app.ai.triage.SORT_PROMPT_VERSION` ("v0" when the module has
none), a fingerprint of the sorting prompt and schema, and the sorting thinking level when
one is set (unset, the folder is the one filled before the setting existed). The first run of a setup sorts and
fills it; every later run with the same setup -- any label, any branch whose sorting is
unchanged -- takes the same sort and makes no sorting call. Reading changes are then measured
without sorting's variance, and the run is much cheaper. A branch that changes the sorting
prompt or its area list gets a new folder automatically (the fingerprint), so it is never
handed sorts made by the old prompt. Note that the frozen sort is whichever models answered
on the first run (see its `accounting`).

## Compare

```
python3 ~/carlink-bench/code/LABEL/apps/bot-service/scripts/bench/compare.py \
    ~/carlink-bench/out/BEFORE.json ~/carlink-bench/out/AFTER.json [--all] [--total-only]
```

For each metric: before and after as `mean [min-max]` over the runs, the change in the mean,
and a verdict. Gemini varies from run to run, so ranges that overlap are **inside noise**;
ranges that don't are **UP** or **DOWN**, marked better or worse where the metric has a
direction. With one run on either side there is no range: the verdict says
`1 run: noise unknown`. Older result files (`{"results", "totals"}`, e.g. the 2026-11-05
baseline) are read too. The per-case tables name the cases (plates): `--total-only` leaves them
out when the output is to be pasted anywhere.

## The result file

```
{
  "label": "baseline-3x",
  "git_ref": "feat/bench-harness@<sha>",
  "args": {...the command line...},
  "models": {"model_chain": "...", "triage_model_chain": "..."},
  "ai_settings": {"thinking_level_read", "thinking_level_sort", "thinking_level_second_look",
                  "image_resolution", "read_tools"},          # null: unset, nothing sent
  "sort_cache": {"dir", "triage_model_chain", "sort_prompt_version", "prompt_fingerprint"} | null,
  "started": "...", "finished": "...",
  "runs": [ one per case per repetition: everything the earlier versions wrote per case
            (parts, parts_with_checklist, checklist, vehicle, calls, answered_by, photos, read,
            kinds, read_kinds, damage_photos_read, sorted_photos, drafted_parts, sort_seconds,
            read_seconds, error) plus
            rep, drafted_items (part, damage_type, condition, condition_tokens, remarks,
            severity, ai_confidence, side_inferred when present),
            parts.matched / parts.side_undetermined,
            accounting {purpose: {calls, failed_calls, seconds, attempts, outcomes, slot_wait_s,
                                  n_429, wait_429_s, by_model {model: {attempts, answered,
                                  seconds, outcomes, [tokens]}}, [tokens]}},
                       [tokens] = prompt_tokens, output_tokens, thought_tokens, tool_calls
                       when Google reported them,
            ai_calls [{purpose, model, ok, seconds, attempts, [tokens], images {resolution: n}}],
            parts_fair {expected, visible_expected, found, visible_found, fair_found,
                        fair_visible_found, neighbour [[drafted, key part]],
                        other_named [[name, key part]], strict_extra, extra, fair_extra,
                        contradicted [[drafted, report wording]] | null},
            sort_cache {hits, misses, stored},
            sorter {tagged_photos, tagged_in_key, label_precision, key_parts,
                    key_parts_photographed, photographed_recall, key_photos, key_photos_read,
                    selection_coverage, not_photographed, photographed_not_read, ...},
            conditions {scored, exact, first_token, token_hits, tokens, token_recall,
                        drafted_conditions, firm_words_only, firm_words_share,
                        words_not_in_firm_reports, details [...]},
            annex_a {real_lines, rows, priced, unpriced, covered, coverage, extra,
                     canonical_rows, canonical_names, perfect_rows, perfect_covered,
                     perfect_coverage} ],
  "summary": {
    "cases_n", "reps", "complete_reps": [1, 2, 3],
    "cases": {"<case>": {"runs", "errors", "metrics": {metric: {mean, min, max, n}}}},
    "total": {metric: {mean, min, max, n}},     # per complete repetition, the sum over cases
    "errors": [{case, rep, error}],
    "accounting": {...merged over every run...},
    "sort_cache": {hits, misses}
  }
}
```

## What the numbers mean

| metric | meaning |
|---|---|
| `found`, `visible_found` | key parts the drafted damage list names (a "(side undetermined)" name counts for its family); visible = the key's non-hidden parts |
| `extra`, `wrong_side` | drafted parts not in the key; a key part named on the wrong side (not counted as found) |
| `fair_found`, `fair_visible_found`, `fair_extra` | the fair score (above): found plus half a part per neighbour; extras less neighbours and Other items named like a key part |
| `neighbour`, `other_named`, `contradicted` | neighbours credited; Other items not counted as extra; drafted parts the report passed as Serviceable |
| `read_tok_in`, `read_tok_out`, `read_tok_think`, `read_code_runs` | the reading call's prompt, output and thinking tokens and code runs (also `sort_tok_*`, `look_tok_*`) |
| `side_undetermined` | drafted "(side undetermined)" names |
| `facts_ok` | vehicle facts read right, of 7 (none of the four photo sets has a log card: 5 is the ceiling) |
| `photos_read`, `sort_s`, `read_s` | photos sent to the reading call; seconds of each stage |
| `calls`, `attempts`, `n_429`, `wait_429_s`, `slot_wait_s` | Gemini calls and model tries; 429s and the seconds waited on the delay Google asked for; seconds waited for a slot in the rate-limit window. Per purpose and model in `accounting` |
| `label_precision` | photos the sorter tagged with a part, whose part is in the key |
| `photographed_recall` | key parts at least one photo is tagged with |
| `selection_coverage` | key-part photos that reached the reading call |
| `cond_exact_rate`, `cond_first_token_rate`, `cond_token_recall` | for found key parts with a surveyor condition (the held-out report's Annex A line of the part's `surveyor` wordings; the first one not Necessary/Serviceable): the drafted `condition` (else `damage_type`) is the same "/" words; has the same first word; contains the surveyor's words |
| `cond_firm_share` | drafted conditions that use only words found in the firm's reports' Condition column |
| `annex_coverage` | the held-out report's Annex A lines covered by the Annex A that prefill (`prefill_annex_a`) builds from the drafted list, with the other reports as history (leave-one-out, matched as `eval_parts_kits._matched`) |
| `annex_extra`, `annex_unpriced`, `annex_canonical` | row wordings that match no real line; rows without a price; rows named exactly like a taxonomy part, which the firm never writes (target 0) |
| `annex_perfect_coverage` | the same with the report's own damaged parts as the damage list: the ceiling that history allows |

Totals add the cases up within each complete repetition (a repetition in which some case
errored is left out of the totals and listed under `errors`); ratios are worked out from the
summed counts, not averaged over cases. With `--reports`, the history (past report lines) in
the scratch database is set to "every report but this case's own" before each case is read as
well as before it is scored, so a reading step that consults past cases is leave-one-out too.

## Offline

```
python scripts/test_benchmark_scoring.py          # from apps/bot-service, any venv with the app's requirements
python scripts/benchmark_intake.py --help
```

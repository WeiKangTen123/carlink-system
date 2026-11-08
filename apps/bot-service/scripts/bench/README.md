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
  `--models CHAIN`, `--triage-models CHAIN`, `--keep-photos`.

Examples:

```
# baseline: master's pipeline (this branch is master + the harness), 3 runs per case
bash ~/carlink-bench/run_bench.sh baseline-3x feat/bench-harness --repeat 3

# a branch that doesn't carry the harness: overlay it from feat/bench-harness
BENCH_HARNESS_REF=feat/bench-harness bash ~/carlink-bench/run_bench.sh a1-3x feat/detection-wording --repeat 3

# reading model A/B on the same frozen sort
bash ~/carlink-bench/run_bench.sh read-36 feat/bench-harness --repeat 3 --models gemini-3.6-flash
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

## The sort cache

`/tmp/bench/sortcache` keeps each photo's sorting result by its sha256, in a folder per sorting
setup: the triage model chain, `app.ai.triage.SORT_PROMPT_VERSION` ("v0" when the module has
none) and a fingerprint of the sorting prompt and schema. The first run of a setup sorts and
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
                                  seconds, outcomes}}}},
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

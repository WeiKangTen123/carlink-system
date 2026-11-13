# parts_mcp

Maps a car (VIN, or make/model/year) to OEM catalogue data: groups ->
exploded diagrams (image, numbered hotspots) -> part rows (OEM number, name,
qty) and reference prices. It is meant for motor-claims surveyors in
Singapore: given the damaged areas of a car ("rear bumper", "tailgate",
"front suspension"), it finds the catalogue diagrams and the part rows to
price.

Two ways in, one engine:

- **The catalogue worker** (`worker.py`): answers Carlink's [Look up in
  catalogue] jobs. In production it runs on the server as the `parts-worker`
  container (see "Running on the server").
- **An MCP server** (`server.py`, `python -m parts_mcp`): the same look-ups as
  tools for an MCP client, for development on a PC.

It fetches on demand, politely, and caches what it fetched, so storage grows
only with the cars actually looked up. Every record carries `source`, `url`
and `fetched_at`. Values a catalogue does not show are `null`. Prices are
shown as the source shows them (currency and tax note included) and are
never converted.

## Sources and brands

| Source | Sites | Brands | Prices |
|---|---|---|---|
| `microfiches` | bmw-spare-parts.com, mini-spare-parts.com, parts-honda.uk (one dealer platform, server HTML) | BMW, MINI, Honda | EUR excl. VAT, from the diagram page |
| `fake` | none (made-up data) | BMW | demo and tests only |

Routing (`config.ROUTES`): BMW, MINI and Honda go to `microfiches`. Any other
make is answered `not_supported`.

- **Microfiches**: BMW / MINI part names are Italian; `name` gets an English
  version from `microfiches_glossary`, the Italian stays in `name_original`.
  Honda names are English. Honda diagram names are wrapped by Honda's own
  data at 25 characters ("DOOR REG ULATOR"); the plug-in joins these back and
  notes the name as shown. It sends an honest User-Agent, obeys robots.txt
  and its Crawl-delay (5 s; the fetcher waits 5-8 s), and stops at 500
  requests a day per site.
- **Partsouq is not in this repository** and not used on the server. From
  the server's address it answers every request with a Cloudflare "Just a
  moment" check, even to a real Chrome (tested 12 Nov 2026), and getting
  past such checks has no place in a public repository. That plug-in
  (Toyota, Nissan, Mazda, Honda Japan, ...) is kept outside the repository on
  the owner's PC. `tests/test_repo_guard.py` fails if any of it comes back.

## Running on the server

The worker runs as a fifth container next to the api
(`infra/docker-compose.prod.yml`, service `parts-worker`, container
`carlink_parts_worker`, image from `tools/parts_mcp/Dockerfile`). The normal
deploy (`infra/deploy.sh`, i.e. `docker compose ... up -d --build`) builds and
starts it. It:

- claims jobs from `http://api:8000` over the compose network (plain http is
  allowed only to `localhost`, `127.0.0.1` and `api`; anything else must be
  https). nginx answers 404 to `/api/catalogue-worker/` from outside, so the
  worker endpoints are reachable from inside the VM only, and they still
  require the token;
- is the only container that contacts a parts site (the three Microfiches
  sites);
- keeps its page cache and catalogue store in the named volume `parts_data`
  (`/data`), so nothing looked up once is fetched again after a rebuild;
- runs as a non-root user, at most 512 MB of memory.

### Switch it on

1. Make a token once: `openssl rand -hex 32`.
2. On the server, put both in `/opt/carlink-system/infra/.env` (git-ignored,
   `chmod 600`):

   ```
   CATALOGUE_ENABLED=true
   CATALOGUE_WORKER_TOKEN=<the token>
   ```

   That file is where they live, not the shell: `deploy.sh` runs
   `docker compose -f infra/docker-compose.prod.yml ...`, whose project
   folder is `infra/`, so compose v2 reads `infra/.env` on every deploy and
   every later `up`, and a value exported in one shell would be gone the next
   time. Compose hands the same `CATALOGUE_WORKER_TOKEN` to the api and to
   the worker; nothing else needs it. The api's `CATALOGUE_MAKES` (default
   `Honda,BMW,MINI`) should name only these makes.
3. Deploy as usual. `docker logs carlink_parts_worker` then shows
   `... polling`, and the dashboard shows the look-up as ready.

To switch it off, set `CATALOGUE_ENABLED=false` in `infra/.env` and deploy: the api answers
404, and the worker logs that once and asks again every 5 minutes, quietly.
Without a token the worker logs one line and idles.

### Logs

```
docker logs --tail 100 -f carlink_parts_worker
```

One line per event, UTC, on stdout (capped by the compose logging options);
the token never appears. For example:

```
2026-11-12T10:42:00Z INFO worker worker server 0.1: sources=['microfiches'] data=/data api=http://api:8000; polling
2026-11-12T10:45:10Z INFO worker job 12: Honda Vezel Hybrid 2017, 3 part(s)
2026-11-12T10:45:16Z INFO fetch live GET host=www.parts-honda.uk status=200 secs=0.84 cache=miss url=https://...
2026-11-12T10:47:02Z INFO worker job 12: {'www.parts-honda.uk': 14} live site request(s) in 112 s
```

When the api cannot be reached (e.g. during a deploy) it waits 30 s, doubling
up to 5 minutes. A result that cannot be delivered is retried for about 17
minutes, then dropped (the api re-queues a silent job after 45 minutes).
`docker stop` / a redeploy stops it at once (SIGTERM); a job it was working on
goes back to the queue after 45 minutes (the api's rule for a silent job).

### Politeness

- One request at a time per site, 5-8 s apart (and never faster than the
  site's robots.txt Crawl-delay), at most 500 requests a day per site. The
  daily count and the last request time live in `pages.db`, so a restart does
  not reset them.
- Catalogue pages are cached for 180 days, prices for 7. A car looked up once
  costs nothing the next time.
- A bot check or the daily cap stops that site for the rest of the job (the
  parts are reported as failed, never retried in a loop). After a 429 the
  next request to that site waits for its Retry-After, at least a minute.

### Seed the cache from a PC (optional)

A PC data folder (`C:\Users\<you>\carlink-parts-data`) already holds pages
looked up there. Copying it saves those requests; it is not needed.

1. On the PC, stop any worker or MCP server using that folder (so the
   databases are complete), and copy `pages.db` (and, if you like,
   `catalogue.db`) to the server, e.g. into `~/seed/`. It may also hold
   Partsouq pages, harmless but unused; to drop them first:
   `sqlite3 pages.db "DELETE FROM pages WHERE url LIKE '%partsouq%'; VACUUM;"`.
2. On the server:

   ```
   cd /opt/carlink-system
   C="docker compose -f infra/docker-compose.prod.yml"
   $C stop parts-worker
   docker cp ~/seed/pages.db carlink_parts_worker:/data/pages.db
   docker cp ~/seed/catalogue.db carlink_parts_worker:/data/catalogue.db   # optional
   # docker cp writes files as root; give them to the worker's user, and drop
   # any write-ahead files that belonged to the old databases
   $C run --rm --no-deps --user root --entrypoint sh parts-worker \
       -c 'rm -f /data/*.db-wal /data/*.db-shm && chown parts:parts /data/*.db'
   $C start parts-worker
   ```

## On a PC (development)

From the repository's `tools` folder, with the parts venv
(`requirements-worker.txt` plus `mcp` and `pytest`):

```powershell
cd <repo>\tools
$py = 'C:\Users\weika\carlink-worktrees\venv-parts\Scripts\python.exe'
& $py -m pytest parts_mcp/tests -q                                   # offline tests
& $py -m parts_mcp.worker --job job.json --out result.json          # one job, no server
& $py -m parts_mcp --sources fake                                    # the MCP server on demo data
```

The whole look-up against a throw-away local api runs with
`apps/bot-service/scripts/e2e_catalogue.py` (it starts the worker with
`--once` and `CARLINK_API_BASE=http://127.0.0.1:<port>`). `run_worker.ps1`
starts the loop with the variables from a git-ignored `worker.env` next to it.
A PC worker cannot serve production: the public URL answers 404 to the worker
endpoints.

| Environment variable | Effect |
|---|---|
| `CATALOGUE_WORKER_TOKEN` | Bearer token, the same value as the api's; required for the loop, never logged |
| `CARLINK_API_BASE` | default `http://api:8000`; https, or plain http to `localhost` / `127.0.0.1` / `api` only |
| `PARTS_MCP_DATA_DIR` | data folder; default `C:\Users\<you>\carlink-parts-data` for the worker (outside OneDrive), `parts_mcp/data` for the MCP server |
| `PARTS_MCP_SOURCES` | comma list of sources (default `microfiches`; `fake` for demo data) |
| `PARTS_MCP_LOG_FILE` | `off`: log to the console only (default: also `<data>/logs/worker.log`) |
| `WORKER_NAME` | name the dashboard shows; default this computer's name |

Worker options: `--once` (ask once, handle at most one job), `--job` /
`--out` (dry run), `--stay-up` (without a token, idle instead of exiting; the
container uses it), `--max-trim-pages N`, `--debug`.

The MCP server speaks MCP over stdio: `command` = the venv python, `args` =
`["-m", "parts_mcp"]`, `cwd` = the `tools` folder. Do not run it and a worker
on the same data folder at once: each paces its own requests, not the
other's.

### MCP tools

| Tool | What it does |
|---|---|
| `list_sources` | Sources loaded (or why not), the brands each serves, the routing |
| `find_vehicle` | Vehicles by VIN / frame number, or by brand + model + year |
| `list_groups` | Top-level groups of a vehicle (Bodywork, UPHOLSTERY, ...) |
| `list_diagrams` | All diagrams of one group |
| `find_diagrams` | Diagrams for a damaged area in plain words (see below) |
| `get_diagram` | One diagram: image URL, hotspots, part rows |
| `search_parts` | Search part rows already fetched for a vehicle |
| `get_price` | Reference price for an OEM number, per source |

Keys returned by one tool (`vehicle_key`, `group_key`, `diagram_key`) are
passed to the next. Errors come back as `{"error": code, "message", "source"}`;
for `challenge_blocked`, `needs_human`, `site_blocked` or
`daily_cap_reached`, do not retry soon.

## What the worker does with a job

A surveyor clicks [Look up in catalogue] (or AI detection queues one), the
api queues a job (make, model, year and damaged-part names only), and the
worker claims it, finds the catalogue car, the diagrams for each part and
their rows, and posts the result back. Shapes and endpoints are in the plan's
`CONTRACT.md` (Job, Result; `POST /catalogue-worker/claim`,
`/jobs/{id}/progress`, `/jobs/{id}/result`, `Authorization: Bearer <token>`).

- **Model name**: the case's first model word (`model_key`), then the full
  model text, then the source's market twin (`MODEL_ALIASES`): parts-honda.uk
  has no Vezel, Fit or Grace, so it uses HR-V, Jazz and City and marks the car
  `twin_of` (same structure; part numbers may differ from the Singapore car).
- **Car**: the `vehicle_key` the surveyor chose, if any; otherwise the cars
  whose years cover the case year, Singapore market first, then Asia /
  General, then the rest; right-hand drive before left; then the catalogue's
  order. Up to 10 go back as `candidates` for "Change car". When the case does
  not name a trim and the model has many (Civic 2008 has 58), it opens at most
  `--max-trim-pages` (8) trim pages, cached ones first, and stops at the first
  Singapore-market car; a note in `failures` says to put the trim in the model
  text to reach the others.
- **Parts**: `find_diagrams` (3 new groups at most, 5 matches), keeps scores of
  0.5 or more, best 3, and `get_diagram` for each. Rows for the other side are
  dropped. `ref_amount` / `ref_currency` / `ref_tax_note` are the price printed
  on the diagram page (EUR, "Unit Price Excl Tax"); no price page is fetched.
  Progress goes to the api after each part, and the worker stops when the api
  says the job was cancelled.
- **Errors**: `not_supported`, `no_vehicle`, `needs_human`,
  `challenge_blocked`, `site_blocked`, `daily_cap_reached`, anything else
  `internal` (one short line, never a stack trace). A failure on one part goes
  in that part's `failures`; after a block or a cap the source is not asked
  again in that job. The job is `done` once the car is found.
- **Caps**: 40 parts, 3 diagrams, 250 rows, 10 candidates, strings of 400
  characters, failures 40 (20 per part). Links that are not https on the three
  Microfiches sites (no user name or password, standard port) become null, so
  the api never refuses a whole result for one odd value. The shared cases
  are `tests/fixtures/catalogue_links.json`, checked on both sides.

### How find_diagrams matches

`synonyms.py` holds concepts (rear bumper, tailgate, front suspension, rear
floor, emblem, mudguard, under cover, parking sensor, ...). Each lists
phrases that mean the area (a query containing one selects the concept),
`also` diagrams that usually hold its parts without naming it (Honda files
the stabilizer under KNUCKLE, the spare-tyre well under FLOOR / INNER
PANELS), and the group names where it usually lives.

- Only the diagram's own name is scored: the query's words 1.0, a synonym
  0.9, an `also` diagram 0.8, a partial match at most half. The score is then
  scaled down when the name is mostly about something else ("SOLENOID VALVE
  EXHAUST CONTROL" for "exhaust" falls below 0.5), and cut to a quarter when
  the name is about a more specific area ("STEERING WHEEL" for "wheel") or
  points the other way (front vs rear). Bracket text only qualifies a name.
- Groups decide which groups are opened and break ties, never the score.
  Groups already listed are searched for free; of the others at most
  `max_new_groups` are opened, ranked by the group name, the concept's group
  hints and the source's own `group_hints` (see below).
- Ties are broken by how close the matching phrase is to the query, then by
  group rank, then by catalogue order. A score of 0.5 or more is a real match.

## Where the data lives

`/data` in the container (volume `parts_data`); on a PC the worker uses
`C:\Users\<you>\carlink-parts-data` and the MCP server `parts_mcp/data/` (or
`--data-dir`). Created on first start; nothing is written at import.

| Path | What |
|---|---|
| `catalogue.db` | Normalized catalogue: vehicles, groups, diagrams, hotspots, part rows, prices, lookups, plus an FTS5 index on part names |
| `pages.db` | Raw page cache (gzip), per-site daily request counts, last request time per site |
| `cookies/<host>.json` | Cookies per site |
| `logs/worker.log`, `logs/parts_mcp.log` | PC only (the container logs to docker logs): one line per live request, cache hit, job and part |

The catalogue stays fresh for 180 days and prices for 7 days
(`config.Settings`). Past that, the source is asked again. If the source
fails, the old answer is returned marked `stale`.

## Tests

Offline, no network, from the `tools` folder:

```powershell
C:\Users\weika\carlink-worktrees\venv-parts\Scripts\python.exe -m pytest parts_mcp/tests -q
```

The pages in `tests/fixtures/microfiches` were saved from the sites and cut
down to what the parsers read (a few list entries, rows and hotspots each).
The sites' terms forbid reproducing their catalogue: keep new fixtures as
small.

## Adding a source

1. Create `parts_mcp/sources/<name>.py`. Subclass `sources.base.Source`, set
   `name = "<name>"`, and implement `brands`, `find_vehicles`, `resolve_vin`,
   `list_groups`, `list_diagrams`, `get_diagram` and `get_price`. The
   contract is in the docstrings of `sources/base.py`.
2. Optionally override the two `find_diagrams` hooks (defaults add nothing):
   `group_hints(vehicle, query)` returns the catalogue's group names that
   usually hold the area, most likely first; `diagram_matches(vehicle, query,
   diagram)` returns True when the catalogue's own naming says the diagram
   shows the area (rated 0.9). Both must be cheap: no network.
3. Add `def create_source(ctx: SourceContext) -> Source`. Get HTTP from
   `ctx.fetcher("<policy>")` and never from your own session. That keeps
   throttling, caps, robots.txt, caching and challenge detection shared.
4. In `config.py`, add the factory to `SOURCE_FACTORIES`, a `FetchPolicy` to
   `POLICIES`, and the brand order to `ROUTES` (and `worker.ROUTES`). Then add
   the name to `DEFAULT_SOURCES` or to `--sources`. A site that needs a
   browser or a bot-check workaround does not belong in this repository.
5. Build records with `fetched_at=result.fetched_at` from the `FetchResult`.
   Put opaque navigation data (such as session-bound URLs) in `source_ref`,
   never in `id`. Raise `NotSupported`, `NotFound` or `ParseError` from
   `core/errors.py`.
6. Test offline against saved, trimmed HTML. `sources/fake.py` shows the
   record shapes. New area words go into `synonyms.CONCEPTS` (concept level,
   with the catalogue names they must reach), not into the plug-in.

## Known limits

- **Makes.** BMW, MINI and Honda only. Other makes are `not_supported`.
- **Prices are references, not Singapore prices.** Microfiches shows EU list
  prices (EUR excl. VAT) for European cars. Many older Honda panels are "not
  sold". Never fill a workshop estimate from them.
- **Market twins.** parts-honda.uk has European types only (no Singapore or
  JDM Vezel; the HR-V is left-hand drive). Part numbers then differ from the
  car being assessed; the sub-part structure is what carries over.
- **Hotspots.** Some Honda pages (e.g. HR-V) have no hotspot markup; callouts
  are only printed in the image.
- **Truncated names.** Honda part names on HR-V pages are cut at 25
  characters ("FACE, R. RR. BUMPER CORNE"); the full French name is in
  `name_original`.
- **Same-named diagrams.** A catalogue may give two diagrams the same name
  (HR-V "FRONT WINDSHIELD / REAR WINDSHIELD" twice, front and rear glass);
  find_diagrams returns both.
- **Name-only matching.** find_diagrams sees diagram names, not their rows:
  parts filed under an unrelated name (HR-V rear wheel-arch protector under
  "SIDE SILL GARNISH / UNDER COVER", end-panel trim under "REAR SIDE LINING")
  are reached only by browsing the group or by `search_parts` after
  `get_diagram`.
- **Not catalogue items.** Number plates, sealants and dealer accessories
  (EU parking sensors) are not in OEM catalogues.

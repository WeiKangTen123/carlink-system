# infra

One VM (project `modular-bot-477206-a9`, Debian, user `weika`, shared with
the Solv and Xero apps) runs two Carlink stacks, each from its own clone:

| Stack | Clone | Compose project | Containers | Port | Address |
|---|---|---|---|---|---|
| production | `/opt/carlink-system` | `infra` | `carlink_api`, `carlink_dashboard`, `carlink_nginx`, `carlink_telegram_bot`, `carlink_parts_worker` | 127.0.0.1:8080 | https://carlink.34-16-92-98.sslip.io |
| staging | `/opt/carlink-staging` | `carlink-staging` | `carlink_staging_api`, `_dashboard`, `_nginx` (+ `_telegram_bot`, `_parts_worker` only on request) | 127.0.0.1:8081 | https://carlink-staging.34-16-92-98.sslip.io (the app's sign-in; signups off; noindex) |

| File | What |
|---|---|
| `docker-compose.prod.yml` | the stack (production as it is; staging adds the overrides below) |
| `docker-compose.staging.yml` | staging's differences: project, names, caps, profiles, AI lane |
| `docker-compose.ai-shared.yml` | the shared AI quota folder (one Gemini key for both stacks) |
| `staging.env.example` | the template of staging's `infra/.env` (names only) |
| `deploy.sh` | the deploy of either stack (`--env prod\|staging --ref <commit>`) |
| `compose.sh` | by-hand `docker compose` for one stack, with the right project and files |
| `migration_check.sh` | a release's database set-up on a throwaway copy (sandbox) |
| `backup.sh` | production backup: local copy, optional offsite copy |
| `restore_test.sh` | test restore of a backup into a throwaway volume (changes nothing) |
| `nginx.conf` | the nginx inside each stack |
| `host-nginx/` | copies of the host nginx sites (`carlink-app.conf`, `carlink-staging.conf`) |
| `tests/` | offline checks (CI): production's compose render, `deploy.sh`, the migration check |

------

## Releases: DEVELOP -> SANDBOX -> STAGING -> PRODUCTION

The owner's order for every system (22 Nov 2026):

```
 DEVELOP     a branch from master, a pull request ("how to test" + the flags list)
    |
    v
 SANDBOX     each change on its own, safely, BEFORE staging. May break; no real
    |        customer data except the throwaway copy in the migration check.
    |          A  smoke of the build: CI green (backend tests, dashboard tsc +
    |             build, guard, infra checks) -- the build in a throwaway context
    |          B  accuracy, only when AI / detection is touched: offline replays
    |             (0 calls), then benchmark runs on a SCRATCH database
    |             (run_bench.sh; they may run inside the staging api container,
    |             never on staging's own data), gate.py
    |          M  migration check: the release's init_db on a copy of production's
    |             database (migration_check.sh --ref <commit>)
    |        all pass -> stage it
    v
 STAGING     the production-like dress rehearsal of the FINISHED release: the
    |        same compose file and images, production's flags (infra/.env)
    |          S  smoke on staging (deploy.sh does it)
    |          C  the owner's acceptance trial (checklist below)
    |          D  the flags list for production (deploy.sh prints the difference)
    |        -> the owner's written "go"
    v
 PRODUCTION  the exact staged files: merge, then deploy.sh --env prod --ref <merge
             commit> (refused unless its files are the staged ones), backup
             first, smoke; flags switched on in production last
```

New AI behaviour ships switched off, is switched on in staging first, then in
production. **Fast lane** (a small urgent fix with no AI or detection
change): CI green + a staging smoke, the owner's OK in chat, then
`--fast-lane "<why>"` if the tree check can't pass.

### Sandbox gate (before staging)

| # | Check | How | Pass when |
|---|---|---|---|
| A | Smoke of the build | the pull request's CI run | `guard`, `backend` (with the infra checks), `dashboard` green |
| B1 | Offline replays (AI PRs) | `BENCH_SCRIPT=replay_links bash run_bench.sh ...` (0 calls) | `gate.py` PASS |
| B2 | Benchmark on a scratch DB (PRs that change what the AI finds) | `BENCH_CONTAINER=carlink_staging_api BENCH_REPO=/opt/carlink-staging bash apps/bot-service/scripts/bench/run_bench.sh <label> <commit> --repeat 3` -- staging lane of the shared key, best in the hours before the reset (12:00-15:45 Malaysia time) | `gate.py` PASS against the baseline |
| M | Migration check | `git -C /opt/carlink-staging fetch origin` (as weika), then `sudo bash /opt/carlink-staging/infra/migration_check.sh --ref <commit>` | PASS; any one-time data step named in the PR (rollback then needs a restore) |

The benchmark inputs (`cases/`, `key.json`, `reports/`) are copied into the
staging api container's `/tmp/bench` (0700) from `~/carlink-bench`, as for
production before (`run_bench.sh` prints the commands when they are
missing; a container recreate empties `/tmp`). Its runs count in staging's
share of the key: 2 a minute, 300 a day (bench included), none while
production is busy (400+ that day).

### Staging (the dress rehearsal)

```bash
bash /opt/carlink-staging/infra/deploy.sh --env staging --ref <commit|branch>
```

Compose always runs with a clean environment: nothing exported in your
shell (a token, a port, a container prefix, a project) reaches either
stack; each takes its values from its own `infra/.env`. Never `export
TELEGRAM_BOT_TOKEN=...` for staging work -- it is ignored anyway, and the
rendered-config check refuses production's token in staging's `.env`.

It refuses before changing anything when: `infra/.env` lacks
`CARLINK_NAME=carlink_staging` / `NGINX_PORT=8081` / the key, or isn't 0600;
the clone is production's or has local edits; the shared AI folder is
missing or production doesn't mount it; or the RENDERED config (`docker
compose config`) isn't staging's -- project, container names, the one port,
volumes, the AI lane, staging's address, and no Telegram or worker token of
production's. It prints the flags that differ from production's (gate D),
checks out the commit (detached), checks the rendered config again, builds
(a failed build puts the old checkout back), starts, restarts nginx, closes
signups, runs the smoke checks and writes `/opt/carlink-staging/.deployed`
(what production's tree check reads).

| # | Staging checklist | Who | Pass when |
|---|---|---|---|
| S | Smoke on staging: health, 401s signed out, worker route 404, `/login`, containers running and not restarted since the deploy, logs clean since `up`, no token in a log, signups closed, https serves `/login` with a robots.txt that keeps search engines out, production still mounts the shared AI folder | `deploy.sh` (again any time: `--smoke-only`; after 5 minutes for restarts) | `smoke: PASS` -- don't use staging (no trial) until the https address serves `/login` |
| C1 | Sign in; Settings pages open | owner | works |
| C2 | New case: upload photos (the seed's placeholders via `--images-out`, or a non-customer photo set), detection, edit parts, file, sign off | owner | works, nothing odd |
| C3 | PDF and Excel download | owner | open and look right |
| C4 | 3D map, case list | owner | loads |
| C5 | The pull request's own feature, from its "how to test" | owner | works as described |
| C6 | Telegram, only if the PR touches the bot: a separate TEST bot (`COMPOSE_PROFILES=telegram` + its token in staging's `.env`) | owner | works |
| D | The `.env` changes production needs (the PR's flags list; deploy.sh's "flags that differ") | lead -> owner | the owner has read them |

Then the owner writes **"go"**.

### Production

1. Bring the pull request up to date with `master` (so the merge has the
   staged files exactly), merge it -> commit M.
2. `bash /opt/carlink-staging/infra/deploy.sh --env prod --ref master` (any
   clone's `deploy.sh` works: it doesn't depend on where it sits). It refuses
   unless M's files are the ones staging last deployed with smoke PASS, backs
   up with the release's `backup.sh` (and checks the copy is fresh), checks
   out M (detached), builds, restarts nginx, runs the smoke checks on 8080,
   writes `/opt/carlink-system/.deployed` and a line in
   `~/carlink-deploy.log`.
3. Switch the flags on in production's `infra/.env` last, then
   `bash /opt/carlink-staging/infra/compose.sh prod up -d` -- it checks the
   rendered config, recreates what changed AND restarts `carlink_nginx`
   afterwards (recreated containers get new addresses; without the restart
   nginx answers 502, as on 2026-10-20) -- and smoke again
   (`deploy.sh --env prod --smoke-only`). Never a bare `docker compose` for
   this.

Exit 2 from deploy.sh = deployed, but a smoke check failed: look at the
output, then `--smoke-only` again or roll back.

### Roll back

- **Code** (about 5 minutes): `bash infra/deploy.sh --env prod --ref <previous> --fast-lane "rollback"`
  (`previous=` in `/opt/carlink-system/.deployed`, or the deploy log). The
  database keeps any new columns; older code ignores them.
- **After rolling forward again** past a rollback to before step 2B (part
  observations): older code does not drop a case's observations when it is
  reopened or deleted, so run
  `docker exec carlink_api python scripts/backfill_part_observations.py --apply`
  once (a dry run first without `--apply`; counts only).
- **Data**: only when data is damaged or a one-time data step must be undone
  -- restore the pre-deploy backup ("Backups" below). Everything written
  since is lost: the owner's call.
- **Staging**: `deploy.sh --env staging --ref <anything>`; to reset its data,
  `bash infra/compose.sh staging down -v` (staging's volumes only:
  `carlink-staging_*`), deploy again, seed again.

### Every day

```bash
bash /opt/carlink-staging/infra/compose.sh staging ps
bash /opt/carlink-staging/infra/compose.sh staging logs --tail 50 api
bash /opt/carlink-staging/infra/compose.sh staging stop      # stays stopped, even after a reboot
bash /opt/carlink-staging/infra/compose.sh staging up -d     # again, same commit (nginx restarted after)
bash /opt/carlink-staging/infra/deploy.sh --env staging --smoke-only
docker exec carlink_staging_api python scripts/seed_staging.py   # missing seed data back
docker exec carlink_staging_api cat /tmp/seed-staging-passwords.txt   # new seed passwords (then rm it)
bash /opt/carlink-staging/infra/compose.sh prod ps | logs ... # production: read-only verbs, up/restart/start, exec
```

Never a bare `docker compose -f infra/docker-compose.prod.yml ...`: in the
staging clone, without `-p` and the staging override, that is production's
project; in production's, without `docker-compose.ai-shared.yml`, production
silently drops the shared AI folder (staging's smoke then fails). And a bare
`up` never restarts nginx. `compose.sh` (and `deploy.sh`) always pass the
right project and files, in a clean environment. For production
`compose.sh` takes only ps logs config top images port events exec stats up
restart start version ls -- never down / rm / kill / stop / volume / build,
nor -v / --volumes / -V / --build (`down -v` would delete the live data).

**Whose scripts run where (one-owner repo, accepted):** `deploy.sh
--fast-lane` runs the release's own `backup.sh` as root with no staging run
behind it, and `compose.sh prod` from the staging clone is the staged
pull request's copy of `deploy.sh` acting on production. Both are fine while
only the owner and the lead push to this repo; run production's commands
from a clone at a reviewed commit when in doubt.

### The shared AI quota (one Gemini key, owner decision 22 Nov)

Google's free limits are per Google Cloud project: one daily allowance per
model (~500 a day on each light model) and one per-minute window for both
stacks. Both stacks keep their AI window, today's request counts (per model,
per lane) and the "day used up" memory in one host folder,
`/opt/carlink-ai-shared` (root, 0700), mounted at `/app/ai-shared`
(`docker-compose.ai-shared.yml`; `app/ai/quota_guard.py`):

| Lane | Rule |
|---|---|
| production | up to 12 requests a minute of its own (`GEMINI_QUOTA_PROD_PER_MINUTE`), 4 s apart; never waits for staging; its base pipeline is never cut |
| staging (app and benchmark) | a request only while the minute's window, with it, holds at most 6 and at most 2 of staging's own (the 14-a-minute budget for both stacks minus production's 12, so the two never pass 14 in any minute); nothing more until the reset (midnight Pacific = 16:00 Malaysia time, 15:00 in US summer) once staging has sent 300 that day, production 400, or a model has fewer than 300 left; never a model with no known daily limit (the strong model's 20 stay production's) |

Every request sent is counted, busy (503) answers and retries included.
Production's extras tier for later features (`quota_guard.extras_tier()`):
RICH above 50% of the 3.5-lite day left, NORMAL 25-50%, LEAN below.

The folder is written by both stacks, and staging runs code under test as
root, so production treats it as untrusted: no lock there is waited for more
than 2 s (then production uses its own window, staging is refused, the day
memory reads "nothing remembered"); no symlink is followed (a planted one is
refused or replaced, never written through); temp files have random names.
The files are versioned (`v1-<key tag>.json`): **a pull request that changes
`quota_guard.py` or `allowance.py`'s files must use new names** (`v2-...`),
or while it is staged the two stacks would rewrite each other's history.

Once the folder exists, production behaves a little differently (accepted):
a busy (503) or per-minute-429 retry takes a slot like any request, so it
counts toward the 12 a minute and waits for the 4 s spacing; each process
merges its own "day used up" memory into the shared one on first use. The
guard pools requests per API key, while Google pools them per Google Cloud
project: two company keys in one project are not counted together (today
there is one).

Today's counts: Settings needs no change -- an admin's `GET /api/system/info`
has `ai_quota` (per key by label, per model per lane, what's left, whether
staging has stopped, the tier). From the server:
`docker exec carlink_staging_api python -c "import json; from app.ai import quota_guard; print(json.dumps(quota_guard.summary(), indent=1))"`.

Without the folder (or before it is mounted) production keeps its own
per-volume files exactly as before, and staging sends nothing. The limits
are settings (`GEMINI_STAGING_*`, `GEMINI_QUOTA_DAY_LIMITS`; defaults in
`app/config.py`), changed in a stack's `infra/.env`.

### First setup of staging (once; the lead, the owner for sudo)

See `plans/2026-11-22-enhance-staging/D2-report.md` "Server steps" for the
annotated order. In short:

1. `ss -ltn | grep -E ':8081\b'` is empty; `free -g`, `df -h /` show room (~3 GB RAM, ~10 GB disk).
2. **sudo**: `sudo install -d -m 700 -o root -g root /opt/carlink-ai-shared`;
   `sudo install -d -o weika -g weika /opt/carlink-staging`.
3. `git clone https://github.com/WeiKangTen123/carlink-system.git /opt/carlink-staging`.
4. `install -m 600 infra/staging.env.example infra/.env`; the Gemini key from
   the running production container through a pipe (never on screen or in a
   command line); production's flag lines appended (the example file has the
   `grep`).
5. **sudo**: the host nginx site `host-nginx/carlink-staging.conf` (no
   basic auth since 23 Nov: the owner chose one sign-in page), `certbot --nginx -d carlink-staging.34-16-92-98.sslip.io`.
   A site that fails `nginx -t` and stays in `sites-enabled` breaks the next
   reload (a certbot renewal) or reboot for **Solv and Xero too**, so every
   `nginx -t` has its undo:
   `sudo nginx -t || sudo rm /etc/nginx/sites-enabled/carlink-staging` --
   after linking the site, and again after certbot. Don't use staging until
   `https://carlink-staging.34-16-92-98.sslip.io/login` answers 200 over
   https (until then a password would go over plain http).
6. `bash infra/deploy.sh --env staging --ref <commit>` (the very first time,
   before production runs the guard: `--allow-unshared-prod`), then
   `docker exec carlink_staging_api python scripts/seed_staging.py`.
7. Production joins the shared folder with its next deploy (the folder
   exists, so `deploy.sh` adds the override); from then on staging deploys
   check that production mounts it.

------

## Backups (production only)

`backup.sh` makes a checked SQLite copy of the live database
(`/root/.carlink-backup/local/carlink-<UTC time>.db`, root-only) and keeps
the newest 14. It runs every night from cron and before every production
deploy (`deploy.sh` stops if it fails). It refuses any volume other than
production's `infra_bot_data`, so a staging stack is never backed up and
never writes to the backup folder or bucket.

Today the copy is **local only**: no bucket is set, and the script says
`offsite copy skipped: BACKUP_BUCKET not set` and exits 0. Settings >
Company then shows the last backup with "uploaded: no". The case photos and
PDFs are not in the local copy; they are only mirrored when a bucket is set.

The record of the last run is `.last_backup` in the data volume (`at`,
`reports`, `local_path`, `uploaded`, `bucket`); the api shows it in
Settings > Company (it never sends `local_path`).

### Run it by hand

```bash
sudo bash /opt/carlink-system/infra/backup.sh
```

### Nightly cron line

In user `weika`'s crontab, which the Solv and Xero apps share: change only
Carlink's line. 19:30 UTC is 03:30 in Malaysia.

```bash
crontab -l > ~/crontab-before-carlink-backup-$(date +%Y%m%d).txt   # keep a copy
crontab -l | grep 'infra/backup.sh'                                 # Carlink's line(s) to be replaced
( crontab -l | grep -v 'infra/backup.sh'
  echo '30 19 * * * sudo -n bash /opt/carlink-system/infra/backup.sh >> "$HOME/carlink-backup.log" 2>&1'
) | crontab -
crontab -l                                                          # the other apps' lines are unchanged
```

The script needs root (it reads the Docker volume and writes under
`/root`), hence `sudo -n`; the log is in weika's home, which the cron job
can write. Check it the next morning: `tail ~/carlink-backup.log`.

### Settings

Environment first, then `/root/.carlink-backup/backup.env` (root-only,
`KEY=value` lines; `sudo` drops the caller's environment, so the file is
the usual place):

| Name | Default | Meaning |
|---|---|---|
| `BACKUP_BUCKET` | not set | bucket for the offsite copy; unset = local copy only |
| `BACKUP_KEEP_LOCAL` | `14` | how many local copies to keep |

Exit codes: 0 = local copy made and checked (and uploaded, when a bucket is
set); 1 = anything else (no local copy, a refused volume, a bucket set
without credentials, or a failed upload). Each error says which.

### Test restore (changes nothing)

```bash
sudo bash /opt/carlink-system/infra/backup.sh          # a fresh copy, so the counts match
sudo bash /opt/carlink-system/infra/restore_test.sh    # newest local copy
sudo bash /opt/carlink-system/infra/restore_test.sh /tmp/carlink-<time>.db   # or a given copy
```

It copies the backup into a new throwaway Docker volume (in a container with
no network), runs `PRAGMA integrity_check`, counts the rows of every table,
counts the live tables read-only, runs the app's `init_db()` on the copy in
a throwaway container (no network, no ports, the checkout's code), prints
restored vs live per table, and removes the volume. PASS = the copy is
intact, `init_db` works, and (for the copy `.last_backup` names) the case
count is what `backup.sh` recorded. Rows changed since the backup are
listed, not failed. Do it once after setting up backups, and again now and
then (e.g. monthly).

### Real restore (the owner's call: everything written since the copy is lost)

```bash
sudo bash /opt/carlink-system/infra/backup.sh          # first, a copy of the current state
VOL=$(docker volume inspect infra_bot_data --format '{{.Mountpoint}}')
docker stop carlink_api carlink_telegram_bot
sudo cp /root/.carlink-backup/local/carlink-<time>.db "$VOL/carlink.db"
sudo rm -f "$VOL/carlink.db-wal" "$VOL/carlink.db-shm"
docker start carlink_api carlink_telegram_bot
```

From the bucket (when one is set):
`gcloud storage cp gs://<bucket>/db/carlink-<time>.db /tmp/` first. Own AI
keys need `ai-keys.secret` from `infra_ai_secrets` when moving servers (see
the header of `backup.sh`).

### Offsite copies (later, only if the owner wants them)

Not set up: the owner chose local-only for now. Cloud Storage's Always Free
tier covers 5 GB-months of Standard storage in `us-central1` (plus 5,000
Class A and 50,000 Class B operations a month), which a database copy a
night (kept 30 days) and the photo mirror should fit for a while. Run with
the owner's OK only, from a PC signed in as the project's owner:

```bash
PROJECT=modular-bot-477206-a9
ACCOUNT=tenweikan@gmail.com
BUCKET=carlink-backups-$PROJECT
SA=carlink-backup@$PROJECT.iam.gserviceaccount.com

# 1. Private bucket in us-central1 (Always Free region); db/ copies deleted after 30 days.
gcloud storage buckets create gs://$BUCKET --project=$PROJECT --account=$ACCOUNT \
  --location=us-central1 --default-storage-class=STANDARD \
  --uniform-bucket-level-access --public-access-prevention
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30,"matchesPrefix":["db/"]}}]}' > lifecycle.json
gcloud storage buckets update gs://$BUCKET --lifecycle-file=lifecycle.json --account=$ACCOUNT

# 2. A service account that can only use this bucket.
gcloud iam service-accounts create carlink-backup --project=$PROJECT --account=$ACCOUNT \
  --display-name="Carlink backup writer"
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --account=$ACCOUNT \
  --member=serviceAccount:$SA --role=roles/storage.objectAdmin

# 3. Its HMAC key, straight into the server's root-only file (never printed).
gcloud storage hmac create $SA --project=$PROJECT --account=$ACCOUNT \
  --format='value(metadata.accessId,secret)' |
gcloud compute ssh instance-20261104-111232 --project=$PROJECT --zone=us-central1-c --account=$ACCOUNT \
  --command='read -r id secret; sudo install -d -m 700 /root/.carlink-backup;
    printf "RCLONE_CONFIG_GCS_ACCESS_KEY_ID=%s\nRCLONE_CONFIG_GCS_SECRET_ACCESS_KEY=%s\n" "$id" "$secret" |
    sudo tee /root/.carlink-backup/hmac.env >/dev/null; sudo chmod 600 /root/.carlink-backup/hmac.env'

# 4. Name the bucket on the server and run a backup (it ends "done ... gs://<bucket>").
gcloud compute ssh instance-20261104-111232 --project=$PROJECT --zone=us-central1-c --account=$ACCOUNT \
  --command="echo BACKUP_BUCKET=$BUCKET | sudo tee /root/.carlink-backup/backup.env >/dev/null &&
    sudo chmod 600 /root/.carlink-backup/backup.env && sudo bash /opt/carlink-system/infra/backup.sh"
gcloud storage ls gs://$BUCKET/db/ --account=$ACCOUNT
```

Then a test restore from the bucket copy: `gcloud storage cp` one
`db/carlink-<time>.db` to the server's `/tmp` and run
`sudo bash infra/restore_test.sh /tmp/carlink-<time>.db`.

From then on a failed upload makes `backup.sh` exit 1, so deploys stop until
it is fixed (`deploy.sh --no-backup` overrides, loudly). To go back to
local-only, delete `backup.env` (the bucket keeps what it has).

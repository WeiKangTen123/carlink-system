# infra

Production runs on one VM (project `modular-bot-477206-a9`, Debian, user
`weika`) from the checkout `/opt/carlink-system`, as Docker Compose project
`infra` (`infra/docker-compose.prod.yml`, 5 containers: `carlink_api`,
`carlink_dashboard`, `carlink_nginx`, `carlink_telegram_bot`,
`carlink_parts_worker`).

| File | What |
|---|---|
| `docker-compose.prod.yml` | the production stack |
| `deploy.sh` | production deploy (backs up first) |
| `backup.sh` | production backup: local copy, optional offsite copy |
| `restore_test.sh` | test restore of a backup into a throwaway volume (changes nothing) |
| `nginx.conf` | the nginx inside the stack |
| `host-nginx/` | copy of the host nginx site |

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

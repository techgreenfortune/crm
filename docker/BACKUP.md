# CRM Prod Backups

Full (DB + files), automated, offsite backups for the Frappe CRM Docker stack.

## Why

The stack (compose project `crm`: `mariadb`, `redis`, `frappe`) keeps the bench
inside the `frappe` container at `/home/frappe/frappe-bench` and the database in
the named Docker volume `mariadb-data`. Nothing is bind-mounted to the host, so a
container or volume loss is total data loss. There was no working backup.

Note: Frappe's built-in **"S3 Backup Settings" DocType was removed** in this
Frappe v17 build, so offsite upload is driven from the host by `backup.sh`
instead of from inside the app.

## What it does

`backup.sh` (run on the docker host):

1. `bench --site crm.localhost backup --with-files` inside the `frappe` container
   — consistent single-transaction DB dump + public/private files.
2. `docker cp` this run's 4 artifacts to the host (`/var/backups/crm/<timestamp>/`).
3. (optional) GPG-encrypt the DB dump.
4. `rclone copy` to S3 / S3-compatible storage.
5. Prune host copies older than `LOCAL_RETENTION_DAYS`.
6. POST to `ALERT_WEBHOOK` on failure.

## One-time setup (on the docker host)

1. **Verify dump tools exist in the container** (the earlier `mariadb-dump not
   found` failure was on a host bench — confirm the container is fine):
   ```sh
   docker exec $(docker compose -p crm ps -q frappe) \
     bash -lc 'which mariadb-dump mysqldump gzip tar'
   ```
   If `mariadb-dump`/`mysqldump` is missing, install `mariadb-client` **in the
   image** (Dockerfile / build step) so it survives a rebuild — a live
   `docker exec apt-get install` is lost on the next `docker compose up`.

2. **Install + configure rclone:**
   ```sh
   curl https://rclone.org/install.sh | sudo bash   # or brew install rclone
   rclone config        # create remote "s3crm" for your provider + bucket
   chmod 600 ~/.config/rclone/rclone.conf
   ```
   Works with AWS S3, Backblaze B2, Wasabi, MinIO (all S3-compatible).
   Lock the IAM credentials to put/list/delete on the one bucket/prefix only.

3. **Bucket hygiene:** enable versioning + a lifecycle rule for remote retention
   (e.g. expire after 30/90 days) — cheaper and more reliable than deleting from
   the script.

4. **Place the script:**
   ```sh
   sudo install -m 755 backup.sh /usr/local/bin/crm-backup.sh
   sudo mkdir -p /var/backups/crm
   ```

## Manual run / first test

```sh
RCLONE_REMOTE="s3crm:greenfortune-crm-backups" /usr/local/bin/crm-backup.sh
rclone ls s3crm:greenfortune-crm-backups | tail
```

## Schedule

**Linux (cron):**
```
0 2 * * * RCLONE_REMOTE="s3crm:greenfortune-crm-backups" /usr/local/bin/crm-backup.sh >> /var/log/crm-backup.log 2>&1
```

**macOS (launchd):** use a `~/Library/LaunchAgents/com.greenfortune.crm-backup.plist`
with `StartCalendarInterval` (cron is deprecated on macOS).

## Config (env vars, see top of `backup.sh`)

| Var | Default | Meaning |
|-----|---------|---------|
| `COMPOSE_PROJECT` | `crm` | compose project name |
| `FRAPPE_SERVICE` | `frappe` | compose service to exec into |
| `SITE` | `crm.localhost` | Frappe site |
| `HOST_BACKUP_ROOT` | `/var/backups/crm` | host staging dir |
| `RCLONE_REMOTE` | `s3crm:greenfortune-crm-backups` | rclone `remote:bucket/prefix` |
| `LOCAL_RETENTION_DAYS` | `7` | prune host copies older than N days |
| `GPG_RECIPIENT` | _(off)_ | encrypt DB dump to this key before upload |
| `ALERT_WEBHOOK` | _(off)_ | POST a JSON alert here on failure |

## Restore drill (DO THIS — a backup is only proven by a restore)

```sh
# pull a backup set down
rclone copy s3crm:greenfortune-crm-backups/<timestamp>/ ./restore-test/

# on a throwaway site
bench new-site test.localhost --mariadb-root-password 123 --admin-password admin --no-mariadb-socket
bench --site test.localhost restore ./restore-test/<...>-database.sql.gz \
  --with-public-files ./restore-test/<...>-files.tar \
  --with-private-files ./restore-test/<...>-private-files.tar
```
Log in and spot-check known data (e.g. lead `CRM-LEAD-2026-00023`).

## Notes

- DB dump is consistent (InnoDB single-transaction) — no downtime needed.
- Do **not** back up by copying the live `mariadb-data` volume — it can be
  inconsistent. Use the logical `bench backup` dump. A raw volume tar is only
  safe with the container stopped, as a secondary measure.
- Keep secrets out of `backup.sh`; rclone config + IAM hold all credentials.

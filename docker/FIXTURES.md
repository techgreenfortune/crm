# Fixtures, Seed Data and Prod Deploys

How records shipped with the app (roles, custom fields, form scripts, lead sources, …)
reach a site, and the step every deploy needs so prod's Desk edits aren't lost.

## Why this needs care

`bench migrate` re-imports every file in `crm/fixtures/` with `force=True`. Frappe
overwrites the record unconditionally — it doesn't compare timestamps. Any change
someone made on prod through Desk to a fixture record is silently reverted on the next
deploy. Upstream Frappe has no merge step; it assumes fixture records are only ever
edited in code.

So records are split by who owns them:

| Kind | Lives in | Applied by | Prod Desk edits |
|---|---|---|---|
| **Code-owned** (Role, Role Profile, Custom Field, Notification, Form Script, Fields Layout, Lead Status, Engagement Status, Call Disposition, …) | `crm/fixtures/*.json`, listed in `crm/hooks.py` → `fixtures` | `bench migrate`, every time, force-overwrite | Lost on deploy unless pulled back first (see below) |
| **Team-maintained lists** (Lead Source, Sub Source, Lost Reason) | `crm/seed_data/*.json` | `crm.install.seed_master_data`, on install and every migrate | Kept — edits, deletes and renames all stick |

A doctype must never be in both. `crm/tests/test_seed_master_data.py` enforces that.

## Deploy checklist

Run from the repo root, on the branch you are about to deploy, **before** building or
deploying. The export has to come from prod while it still runs the old code, because
the deploy's migrate overwrites the edits you want to keep.

1. **Report what prod changed**

   ```bash
   python3 crm/scripts/pull_prod_fixtures.py <prod-ssh-host>
   ```

   Over ssh, inside `crm-prod-frappe-backend-1`, the script:
   - reads the fixtures prod currently ships, which is the merge base;
   - exports prod's live DB for every doctype in your working-tree `hooks.py` `fixtures`
     list, into a temp dir (it never writes into the app folder);
   - three-way merges the export into `crm/fixtures/`, matching records by `name`.

   Nothing is written yet. The output lists, per file:
   - `taken from prod` — field changed only on prod, will be pulled in;
   - `added on prod` — record created on prod, will be added;
   - `CONFLICT (kept ours)` — same field changed differently on prod and in code;
   - `note` — e.g. a record deleted on prod (never removed automatically).

2. **Apply it**

   ```bash
   python3 crm/scripts/pull_prod_fixtures.py <prod-ssh-host> --write
   ```

3. **Resolve conflicts.** The script keeps our value and exits 1. Decide per field, edit
   the file in `crm/fixtures/` by hand, and re-run step 1 until it exits 0.

4. **Review and commit** the `crm/fixtures/` diff (`chore: pull prod fixture edits`).

5. **Deploy** as usual. Migrate then imports fixtures that already contain prod's edits,
   and `seed_master_data` inserts only seed records the site has never received.

Defaults are `--container crm-prod-frappe-backend-1`, `--site crm.indiframe.com` and
`--bench-dir /home/frappe/frappe-bench`; override them for other environments. Add
`--keep <dir>` to keep the pulled `export/` and `deployed/` folders for inspection.

### Exit codes and messages

| Message | Meaning / action |
|---|---|
| `base: container apps/crm HEAD <sha>` | Good. Base read from the container's git commit. |
| `WARNING: no git in container; base is … unverified` | The image has no `.git`, so the base is whatever files sit in the container's `apps/crm/crm/fixtures`. That's correct unless someone ran `bench export-fixtures` inside the container. If unsure, re-run with `merge_prod_fixtures.py --base <git ref of the deployed commit>` (below). |
| `EXPORT FAILED, prod edits NOT checked for: X` (exit 1) | A doctype couldn't be exported. Its prod edits would be overwritten. Fix the cause; if X simply doesn't exist on prod yet (new doctype in this release), re-run with `--allow-missing "X"`. |
| `N conflict(s)` (exit 1) | See step 3. |

### Manual merge from an existing export

```bash
python3 crm/scripts/merge_prod_fixtures.py <export_dir> --base <git-ref>     # base from git
python3 crm/scripts/merge_prod_fixtures.py <export_dir> --base-dir <dir>     # base from files
```

**Never** run `bench export-fixtures` inside the prod container. It writes into the
app folder (the merge base) and only knows the deployed `hooks.py` list.

## Seed data

`seed_master_data` keeps a list of every seed record it has applied, in the global
default `crm_applied_seed_records`. Each record is inserted **at most once per site**:

- Add a record to `crm/seed_data/*.json` → inserted on every site at its next migrate.
- Edit, rename or delete a record on a site through Desk → stays that way.
- Remove a record from `seed_data` → nothing happens on existing sites. Delete it there
  by hand if needed.
- Changing a field of an already-seeded record in `seed_data` does **not** update existing
  sites. Edit it on the site, or use a `bench console` snippet.

File order matters: sub sources link to lead sources, so `SEED_FILES` in
`crm/install.py` lists lead sources first.

### First migrate on an existing site

A site that has no list yet (any site installed before this change) only **records** the
whole seed set on its first migrate and inserts nothing. Everything seeded so far
reached it as force-imported fixtures, so a missing record was deleted on purpose.

The catch is a site whose fixtures were out of date when it got this change (an old dev
or UAT copy). It never received some newer records, but they get marked as applied
anyway. To backfill such a site, treat every seed record as new once:

```python
# bench --site <site> console
frappe.defaults.set_global_default("crm_applied_seed_records", "[]")
from crm.install import seed_master_data
seed_master_data()
frappe.db.commit()
```

This also re-creates records deliberately deleted on that site, so don't run it on prod.

## Inspecting prod by hand

No bench on the host PATH:

```bash
ssh <prod-ssh-host>
docker exec -it crm-prod-frappe-backend-1 bash
bench --site crm.indiframe.com console
```

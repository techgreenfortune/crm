# Merge Notes — upstream sync guide

> Reference for the moment **frappe/crm PR #2120** (`feat: sales hierarchy
> permissions for leads and deals`, branch `user-hierrarcy-fe`) lands on
> the `frappe/crm` branch that `custom/develop` tracks, and we converge.
>
> **Status update 2026-05-22 — CORRECTED:** `custom/develop` was forked from
> `frappe/crm:main`, NOT `frappe/crm:develop`. `git merge-base custom/develop
> upstream/main` is ~57 commits behind; `upstream/develop` is 2971 commits
> behind. The right sync source for us is `main` / `main-hotfix`, never
> `develop`.
>
> PR #2120 merged into `upstream/develop` on 2026-05-21 (commit `5508e1f`).
> It is **NOT on `upstream/main` or `upstream/main-hotfix`** as of writing.
>
> **Bridge:** Backport PR #2215 (Mergify auto-PR onto `main-hotfix`) carries
> the same code prepared for the main line. Status: OPEN. Once it merges,
> `main-hotfix` carries the change; the merge procedure below becomes runnable
> against `upstream/main-hotfix` (or `upstream/main` after the next release
> cut).
>
> **Until PR #2215 lands, do NOT attempt the merge procedure.** Merging
> `upstream/develop` would drag in ~3000 unrelated commits. Our cherry-pick
> remains the only path on the main line.
>
> The "Open question" section at the bottom is closed: decision is `merge
> via PR #2215 path, don't revert`.

## Why this file exists

We **cherry-picked PR #2120 before it merged upstream** to unlock admin-editable
Sales Hierarchy on our timeline. That decision was deliberate; the cost is
known merge friction when upstream eventually merges. This file enumerates
the conflict surface so the merge is mechanical, not archaeological.

## Cherry-pick provenance

PR #2120 head when we copied from it: `262477886174cc7520748fb078e86e602b9ae818`
(`chore: use shorter title`, 2026-05-21).

Upstream merge commit on `frappe/crm:develop`: `5508e1f428575b0edbdb2e020293279cccd45d0c` (2026-05-21 11:40 UTC). If you `git log 262477886174cc7520748fb078e86e602b9ae818..5508e1f` against the upstream tree, any non-empty diff is reviewer-requested change that needs reconciling against our copy.

If the PR rebased or accepted reviewer feedback before merge, our copies of
the Category A files may drift from the merged version. Default action at
merge time: **take upstream's version** for everything in Category A.

---

## Category A — files copied verbatim from PR #2120

These will appear as "both added" conflicts on merge. Always take upstream's
version (`git checkout --theirs <path>`):

- `crm/fcrm/doctype/crm_sales_hierarchy/__init__.py`
- `crm/fcrm/doctype/crm_sales_hierarchy/crm_sales_hierarchy.json`
- `crm/fcrm/doctype/crm_sales_hierarchy/crm_sales_hierarchy.py`
- `crm/fcrm/doctype/crm_sales_hierarchy/crm_sales_hierarchy.js`
- `crm/fcrm/doctype/crm_sales_hierarchy/crm_sales_hierarchy_tree.js`
- `crm/fcrm/doctype/crm_sales_hierarchy/test_crm_sales_hierarchy.py`
- `crm/permissions/__init__.py`
- `crm/permissions/org_hierarchy.py`
- `crm/permissions/test_org_hierarchy.py`
- `frontend/src/components/Settings/Hierarchy/Hierarchy.vue`
- `frontend/src/components/Settings/Hierarchy/HierarchyRow.vue`
- `frontend/src/components/Settings/Hierarchy/UserMultiSelect.vue`
- `frontend/src/components/Settings/Hierarchy/useDragDrop.js`
- `frontend/src/components/Settings/Hierarchy/useRemoveNode.js`
- `docs/user-hierarchy.md`

## Category B — files we patched in PR #2120's style

Upstream's merge will introduce the same patches. Likely hunk-level conflict;
resolve by accepting upstream where the patches are identical. Watch for our
custom additions in the same area.

| File | Our edit | Merge action |
|---|---|---|
| `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` | Added `enable_sales_hierarchy` Check field | Accept upstream's identical patch; verify no duplicate field. |
| `crm/api/user.py` | Added hierarchy-aware `update_user_role` + `remove_crm_roles_from_user` blocks | Accept upstream's version of those blocks. Note: our `_NON_MANAGERIAL_PROFILES` constant and the 13-role profile flow are ours; preserve those. |
| `frontend/src/components/Settings/Settings.vue` | Added `Hierarchy` menu entry + `LucideNetwork` icon import | Accept upstream's identical lines. |

## Category C — the high-risk merge point

### `crm/hooks.py`

Upstream's merge will wire `CRM Lead` to its own permission hook. **We must
keep `CRM Lead` pointing to OUR override** (the 13-role matrix). Accept
upstream's CRM Deal wiring; keep ours for CRM Lead.

**Target state post-merge:**

```python
permission_query_conditions = {
    # CRM Lead: our 13-role matrix list filter (Python hook — see
    # crm/overrides/crm_lead_permissions.py:get_permission_query_conditions).
    # Replaces the legacy "CRM Lead — Permission Query" Server Script which
    # crashed in safe_exec on frappe.get_attr. Upstream's
    # get_lead_permission_query_conditions is DROPPED on our fork.
    "CRM Lead": "crm.overrides.crm_lead_permissions.get_permission_query_conditions",
    "CRM Deal": "crm.permissions.org_hierarchy.get_deal_permission_query_conditions",
}

has_permission = {
    # CRM Lead: our 13-role matrix (preserves B2F C7-only, Estimation C2-only,
    # Calling Team / JSE pool logic, ASM/RSM downstream scoping, Marketing
    # read-all, SE/PSE Retail/Projects split). Do NOT replace with upstream's
    # flat subtree hook. Lives in the same module as the list filter above so
    # the two gates stay in lockstep.
    "CRM Lead": "crm.overrides.crm_lead_permissions.has_permission",
    "CRM Deal": "crm.permissions.org_hierarchy.has_deal_permission",
}

doc_events = {
    # ... our existing events ...
    "CRM Sales Hierarchy": {
        "on_update": ["crm.overrides.crm_lead_permissions.bust_downstream_users_cache"],
        "on_trash":  ["crm.overrides.crm_lead_permissions.bust_downstream_users_cache"],
    },
}
```

Resolution recipe at merge time:
1. Take upstream's `CRM Deal` wiring (both keys).
2. Reject upstream's `CRM Lead` entries — keep our `crm.overrides.crm_lead_permissions.*` for both `permission_query_conditions["CRM Lead"]` and `has_permission["CRM Lead"]`.
3. Keep our `CRM Sales Hierarchy.on_update/on_trash` doc_events.

## Category D — pure-ours, no upstream conflict

PR #2120 doesn't touch these. They will pass through the merge untouched:

- `crm/overrides/crm_lead_permissions.py` — 13-role matrix, `has_permission`,
  `get_permission_query_conditions` (replaces the deleted Server Script),
  `_downstream_users`, `bust_downstream_users_cache`.
- `crm/fcrm/doctype/crm_lead/crm_lead.py` — `_check_write_permission` with
  Calling Team unassigned-claim, ASM cross-team guard, Won lock, quote-freeze.
- `crm/fixtures/server_script.json` — Stage Transition + disposition validation
  + after-save side effects. (The 13-role Permission Query for CRM Lead is
  NOT here anymore — it lives in Python in `crm_lead_permissions.py`.)
- `crm/install.py` — Won engagement status + lead status seeding.
- `crm/api/projects.py`, `crm/api/quotes.py` — new endpoints.
- Doctype JSONs unrelated to hierarchy (call_log, lead, quote_request, etc.).

## Merge procedure (when PR #2215 lands)

**Pre-check (do this every time before starting):**

```sh
# Confirm PR #2215 has merged into upstream/main-hotfix (or that the next
# release already merged main-hotfix → main).
gh pr view 2215 --repo frappe/crm --json state,mergedAt,mergeCommit

# Then fetch the latest upstream state.
git fetch upstream main main-hotfix
git log upstream/main..upstream/main-hotfix --oneline | head -20   # delta to expect
```

If `gh pr view 2215` still shows `OPEN` — STOP. Do not merge `upstream/develop`
as a workaround; it would bring ~3000 commits of unrelated work onto our
main-line fork. Wait for PR #2215 (or check whether a fresh PR was opened by
upstream maintainers if Mergify's auto-PR got closed without merge).

**Merge steps (run only once the pre-check passes):**

1. Sync `custom/develop` with `origin` and create an integration branch:
   ```sh
   git checkout custom/develop && git pull origin custom/develop
   git checkout -b integration/upstream-2120-merge
   ```

2. Delete our Category A files first (these are about to arrive from upstream):
   ```sh
   git rm -r crm/fcrm/doctype/crm_sales_hierarchy crm/permissions \
              frontend/src/components/Settings/Hierarchy docs/user-hierarchy.md
   git commit -m "chore: drop pre-merge copy of PR #2120 files (preparing for upstream sync)"
   ```

3. Merge from the branch that actually carries the change:
   - If PR #2215 merged but no `main` release has cut yet:
     `git merge upstream/main-hotfix`
   - If a release has happened and PR #2120 is now on `main`:
     `git merge upstream/main`
   Either way, Category A files arrive cleanly; Category B surfaces hunk
   conflicts; Category D passes through.

4. Resolve Category B conflicts file-by-file (Settings.vue, fcrm_settings.json,
   api/user.py).

5. Resolve `crm/hooks.py` per the recipe above.

6. `bench --site crm.localhost migrate` and re-run the verification matrix:
   - Sales Hierarchy tree still renders at `/app/crm-sales-hierarchy`.
   - `_downstream_users("paresh@indiframe.com")` returns expected subtree
     from `bench console`.
   - CRM Lead permissions still honor the 13-role matrix.
   - CRM Deal now uses upstream's hierarchy hook (toggle via
     `FCRM Settings.enable_sales_hierarchy`).

7. Re-run tests: `bench --site crm.localhost run-tests --app crm`.

8. Once green, fast-forward `custom/develop`:
   ```sh
   git checkout custom/develop && git merge --ff-only integration/upstream-2120-merge
   ```

## Open question — is the cherry-pick still worth it? — **CLOSED 2026-05-21**

Resolved before the question could go cold: upstream merged PR #2120 on the
same day we cherry-picked (2026-05-21 11:40 UTC). The cherry-pick window was
hours, not weeks. **Decision: merge, don't revert.** Run the "Merge procedure"
section above on the next bench session.

The revert recipe below is kept for historical reference only — if anything
goes wrong during the merge and we need to back out the cherry-pick to
sync from a clean slate, this is the path. Otherwise ignore.

<details>
<summary>Historical revert recipe (do not run unless the merge fails)</summary>

1. `git rm` all Category A files.
2. Revert Category B patches (the upstream-style hunks).
3. Restore `crm/hooks.py` to drop the CRM Deal wiring.
4. Re-introduce the hardcoded `_REPORTS_TO` dict in `crm_lead_permissions.py`
   as a stopgap (see git history around `2026-05-21` for the dict).

</details>

Track upstream status: `gh pr view 2120 --repo frappe/crm` (state: MERGED).
Backport: `gh pr view 2215 --repo frappe/crm` (state: OPEN on `main-hotfix`).

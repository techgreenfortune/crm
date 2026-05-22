# Merge Notes — upstream sync guide

> Reference for the moment **frappe/crm PR #2120** (`feat: sales hierarchy
> permissions for leads and deals`, branch `user-hierrarcy-fe`) lands on
> `frappe/crm:develop` and we sync it into `custom/develop`.

## Why this file exists

We **cherry-picked PR #2120 before it merged upstream** to unlock admin-editable
Sales Hierarchy on our timeline. That decision was deliberate; the cost is
known merge friction when upstream eventually merges. This file enumerates
the conflict surface so the merge is mechanical, not archaeological.

## Cherry-pick provenance

PR #2120 head when we copied from it: `262477886174cc7520748fb078e86e602b9ae818`
(`chore: use shorter title`, 2026-05-21).

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
    # CRM Lead: NOT wired here — our Server Script "CRM Lead — Permission Query"
    # handles list filtering. Upstream's `get_lead_permission_query_conditions`
    # is DROPPED on our fork.
    "CRM Deal": "crm.permissions.org_hierarchy.get_deal_permission_query_conditions",
}

has_permission = {
    # CRM Lead: our 13-role matrix (preserves B2F C7-only, Estimation C2-only,
    # Calling Team / JSE pool logic, ASM/RSM downstream scoping, Marketing
    # read-all, etc.). Do NOT replace with upstream's flat subtree hook.
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
2. Reject upstream's `CRM Lead` entries.
3. Keep our `CRM Sales Hierarchy.on_update/on_trash` doc_events.

## Category D — pure-ours, no upstream conflict

PR #2120 doesn't touch these. They will pass through the merge untouched:

- `crm/overrides/crm_lead_permissions.py` — 13-role matrix, `_downstream_users`,
  `bust_downstream_users_cache`.
- `crm/fcrm/doctype/crm_lead/crm_lead.py` — `_check_write_permission` with
  Calling Team unassigned-claim, ASM cross-team guard, Won lock, quote-freeze.
- `crm/fixtures/server_script.json` — 13-role-aware Permission Query +
  Stage Transition + disposition validation.
- `crm/install.py` — Won engagement status + lead status seeding.
- `crm/api/projects.py`, `crm/api/quotes.py` — new endpoints.
- Doctype JSONs unrelated to hierarchy (call_log, lead, quote_request, etc.).

## Merge procedure (when the day comes)

1. `git fetch upstream develop` (or wherever PR #2120 lands).
2. From `custom/develop`, delete our Category A files first:
   ```sh
   git rm -r crm/fcrm/doctype/crm_sales_hierarchy crm/permissions \
              frontend/src/components/Settings/Hierarchy docs/user-hierarchy.md
   git commit -m "chore: drop pre-merge copy of PR #2120 files (preparing for upstream sync)"
   ```
3. `git merge upstream/develop` — Category A files arrive cleanly; Category B
   surfaces hunk conflicts; Category D passes through.
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

## Open question — is the cherry-pick still worth it?

If PR #2120 is taking longer to merge than expected (months, not weeks), the
calculus may shift toward reverting our cherry-pick and waiting. To revert:

1. `git rm` all Category A files.
2. Revert Category B patches (the upstream-style hunks).
3. Restore `crm/hooks.py` to drop the CRM Deal wiring.
4. Re-introduce the hardcoded `_REPORTS_TO` dict in `crm_lead_permissions.py`
   as a stopgap (see git history around `2026-05-21` for the dict).

Track PR #2120 status via `gh pr view 2120 --repo frappe/crm`.

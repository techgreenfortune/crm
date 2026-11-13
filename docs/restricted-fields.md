# Restricted Field Visibility — Plan

Jira: [INDF-2009](https://indiframe.atlassian.net/browse/INDF-2009) — "CRM: % Margin visibility at user level".

> Restrict view access of the margin % field on quotation requests to certain
> roles/users. Right now it's available to everyone and sales executives are
> misusing it.

## Decisions

| Topic | Decision |
|---|---|
| What is restricted | **View** only. Estimation Team keeps filling margin; write paths, QR→Lead sync and C4 validation are untouched. |
| Default | Everyone sees margin (current behaviour). Nothing changes until an admin adds a rule. |
| Who configures | System Manager, from CRM Settings → Restricted Fields. |
| Granularity | Per role **or** per user. Roles can't express "hide from this one SE", so rules live in FCRM Settings, not in docperms. |
| Always visible | System Manager, Estimation Team, Administrator — no rule can hide fields from them. Every other role, including all manager roles (Sales Head, Sales Coordinator, Management, RSM, ASM), is hideable by role or per-user rule. |
| Scope | Generic "field groups". Margin is the first group; a new sensitive field on any doctype = one `FIELD_GROUPS` entry, no controller code. |
| Enforcement | At Frappe's own field-permission chokepoints, so every API that honours permlevels honours rules too — CRM UI, `/api/resource`, `frappe.client.*`, reportview/export and `/app` desk (CRM users are System Users; there is no desk gate). |
| Notifications / comments | Margin removed from the "Quote Received — Review & Share" notification for everyone and no longer written into upload comments. "Quote Accepted" keeps it (goes only to Estimation Team, exempt). Legacy comments are masked at read time. |
| Precedence | Rules only **remove** access, never grant. A docperm/permlevel denial still applies; a rule hides a field even if docperms grant it. |

## Model

```
FCRM Settings
  restricted_field_rules  (Table, permlevel 1 — System Manager read/write)
    CRM Restricted Field Rule (child)
      field_group : Data   — must be a key of FIELD_GROUPS
      applies_to  : Select — Role | User
      role        : Link Role  (when applies_to = Role)
      user        : Link User  (when applies_to = User)
```

Field groups are code-owned (`crm/permissions/field_visibility.py`):

```python
FIELD_GROUPS = {
    "Quote Margin": {
        "fields": {
            "CRM Quote Request": ("quote_margin",),
            "CRM Lead": ("custom_final_margin",),
        },
        # legacy upload comments only; new ones carry no margin
        "comment_patterns": (r",?\s*Margin:\s*[-\d.,]+\s*%",),
    },
}
```

Always-visible roles live in `crm/permissions/role_config.py`
(`RESTRICTED_FIELD_EXEMPT_ROLES`) — it is the role SSOT.

## Single decision point

`get_hidden_fields(doctype, user=None) -> frozenset[str]` — every enforcement
point calls only this. Administrator / exempt role → nothing hidden; otherwise
the union of field-group fields for rules matching the user or any of their roles.

- Fast path: returns empty for doctypes not in any group (`RESTRICTED_DOCTYPES`),
  since the patches below run for every doctype.
- Rule resolution is `@request_cache`d per user; `clear_cache()` is called on
  FCRM Settings `on_update` so a same-request rule change is seen immediately.

## Enforcement — Frappe chokepoints

`field_visibility.install()` patches five Frappe methods by fieldname (plus
`BaseDocument.__getstate__`, see below). It is idempotent and runs from the
`before_request` and `before_job` hooks (so Frappe-native report/export jobs that
never import CRM code are covered) and on `import crm` (console, tests).

| # | Patched method | Covers |
|---|---|---|
| 1 | `Meta.get_permitted_fieldnames` | `get_list`, `frappe.client.get_list/get_value`, `/api/resource` lists, `reportview.get`, `export_query`, group-by counts. Hidden fields are silently dropped; `"*"` expands without them |
| 2 | `Document.apply_fieldlevel_read_permissions` | `frappe.client.get`, desk `getdoc`, REST read — removes hidden values (incl. child rows) and marks the doc |
| 3 | `BaseDocument.as_dict` | Pops marked fields so they are **absent**, not a fake `0` (`get_valid_dict` re-adds missing floats as 0). `db_update` uses `get_valid_dict`, so DB writes are unaffected |
| 4 | `Document.validate_higher_perm_levels` | Whole-doc save/`set_value` by a hidden user: hidden fields are reset to the DB value, so an echoed `0`/`None` is not an edit. Runs before controller hooks, so in-save syncs (`_sync_accepted_quote_on_won`) still write. Skipped for `ignore_permissions`, install and new docs (`request_quote` seeds margin on insert) |
| 5 | `Document.run_post_save_methods` | Marks the doc after save hooks, so `client.set_value`/`save`/`insert` and REST PUT/POST responses omit hidden fields |

`frappe.get_all` and `ignore_permissions` server code bypass all of this by design.

**Post-save mark.** After a hidden user's save the doc stays marked for the rest of
the request, so any same-request `doc.as_dict()` omits hidden fields. The mark is
dropped on pickling (`__getstate__`), so enqueued jobs, webhooks and caches get full
values. Same-request server code that needs the full dict wraps it in
`with field_visibility.unmasked(): ...`.

**Upgrade guard.** Wrappers keep `__wrapped__` and a tag. Tests assert all patches are
installed and that Frappe's original signatures still match (`user` keyword-only on
`get_permitted_fieldnames`, `self`-only for the rest). A Frappe upgrade that changes
them fails CI instead of silently disabling enforcement.

### CRM-specific points (not reachable through the chokepoints)

| Path | Change |
|---|---|
| `crm.api.doc.get_data` (list / kanban / group-by / map) | Drop hidden fields from columns/rows/filter fields and strip values. Kanban `column_field` or `group_by_field` on a hidden field → `PermissionError` |
| `crm.api.doc.get_group_by_fields` | Hidden fields not offered |
| `crm.api.doc.export_pdf` | Drop hidden fields |
| `crm.api.activities.get_lead_activities` / deal | Skip Version changes on hidden fields, mask legacy comments, sanitise `docinfo` |
| `crm.api.activities.get_quote_revisions` | Margin read from the matched QR's `quote_margin` (legacy comments parsed as fallback); `None` when hidden |
| Layout (`handle_perm_level_restrictions`) | Marks hidden fields `hidden=1` (it uses its own permlevel check) |
| Frontend `checkMandatory` | Treats hidden fields as hidden so an SE can still move a lead to C4 / save a QR |

## Admin API

- `crm.permissions.field_visibility.get_restricted_field_settings()` — System Manager only. Returns field groups + current rules.
- `crm.permissions.field_visibility.save_restricted_field_rules(rules)` — System Manager only.
- `crm.permissions.field_visibility.get_my_hidden_fields()` — any user. `{doctype: [fieldnames]}` for the frontend.

## Frontend

- `stores/restrictedFields.js` — loads `get_my_hidden_fields` once, created in `useDocument`. `ensureLoaded()` awaits the first fetch.
- `utils/fieldTransforms.js` — pure `withHiddenOverrides(overrides, hidden)`; unit-tested.
- `data/document.js` — async `checkMandatory` awaits `ensureLoaded()` before using it (otherwise the first C4 save read the empty initial data and failed on "Latest Quoted Margin").
- `components/Settings/RestrictedFieldsSettings.vue` — rules editor, under System Configuration, visible to `isAdmin()`.
- `QuoteArea.vue` already hides margin when `null` (`v-if`).

## Tests

Backend `crm/tests/test_restricted_fields.py` (25 tests):
- rule resolution: no rules, role, user, exempt role, Administrator, manager roles hideable.
- validation: unknown group and rule without role/user rejected; non–System Manager can't save.
- doc load: hidden key absent (not `0`); other users still see it.
- generic APIs: `frappe.client.get_list` / `get_value`, `frappe.get_list(fields=["*"])`, `reportview.get`.
- save: whole-doc save and `client.set_value` keep the DB value; response omits the field; `ignore_permissions` save not reverted.
- get_data strip; group-by/kanban on hidden field blocked.
- notification and upload comment carry no margin; revisions read margin from QR.
- activity feed, layout.
- patches installed, Frappe signatures unchanged, install wired to `before_request`/`before_job`, save mark dropped on pickle and bypassed by `unmasked()`.

Frontend: `withHiddenOverrides` in `tests/unit/`.

## Known limitations

- Filtering/sorting by a hidden field (via `filters` / `order_by`) could let a user infer values by probing. Not blocked.
- `listview.get_group_by_count` on a hidden field drops the value column, but per-group counts are still computed.
- Frontend caches (meta, layout, hidden fields) are per browser; after a rule change users pick it up on reload.
- The patches wrap Frappe internals (verified on v15.107). The signature test catches signature changes, not behavioural ones — re-run `test_restricted_fields` on every Frappe upgrade.
- `ignore_permissions` saves skip the restore. Server code that saves a whole doc **received from a hidden user's client** with `ignore_permissions` would write the missing value as `None`. Nothing does this today; don't add it.

## Extending later

| Need | Change |
|---|---|
| Hide another field (any doctype) | Add a `FIELD_GROUPS` entry |
| Read-only instead of hidden | Add `mode` column; layout sets `read_only`, save guard already resets |
| Stage-conditional | Add optional `status` column; `get_hidden_fields` takes `doc` |
| Allow-list exceptions | Add `effect` (Hide/Allow) column; user row overrides role row |

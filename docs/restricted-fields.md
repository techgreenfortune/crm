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
| Always visible | System Manager, Management, Estimation Team, Administrator — no rule can hide fields from them. Sales Head is hideable (role or per-user rule). |
| Scope | Generic "field groups". Margin is the first group; new sensitive fields = one mapping entry. |
| Enforcement | All CRM UI/API paths. `/app` desk is admin-only, so out of scope. |
| Old comments | Not rewritten in the DB. Masked at read time for hidden users. |

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
        "comment_patterns": (r",?\s*Margin:\s*[-\d.,]+%",),
    },
}
```

Always-visible roles live in `crm/permissions/role_config.py`
(`RESTRICTED_FIELD_EXEMPT_ROLES`) — it is the role SSOT.

## Single decision point

`get_hidden_fields(doctype, user=None) -> frozenset[str]` — every enforcement
point calls only this. Rules: Administrator / exempt role → nothing hidden;
otherwise union of field-group fields for rules matching the user or any of
their roles.

Helpers built on it: `strip_doc`, `strip_rows`, `mask_comment`,
`restore_hidden_on_save`.

## Enforcement points

| # | Path | Change |
|---|---|---|
| 1 | Doc load (`frappe.client.get`, `/api/resource`, getdoc) | Override `apply_fieldlevel_read_permissions` on `CRMLead` and `CRMQuoteRequest` |
| 2 | Whole-doc save by a hidden user | `validate` (first line) always restores hidden fields to their before-save value. `as_dict` re-adds stripped numeric fields as `0`, so the client echoes `0` back — treated as no edit. In-save syncs (`_sync_accepted_quote_on_won`) run later, `db.set_value` syncs bypass it |
| 3 | `crm.api.doc.get_data` (list / kanban / group-by / map) | Strip hidden values from rows; drop hidden fields from columns, rows and the filter/sort field list |
| 4 | `crm.api.doc.export_pdf` | Drop hidden fields |
| 5 | `frappe.desk.reportview.export_query` (list Export) | No change: the CRM UI builds export fields from `get_data` columns, which are already stripped |
| 6 | `crm.api.quotes.list_lead_quote_requests` | Strip `quote_margin` |
| 7 | `crm.api.activities.get_quote_revisions` | `margin = None` |
| 8 | `crm.api.activities.get_lead_activities` | Skip Version changes on hidden fields, mask comments, sanitise the `docinfo` response payload too |
| 9 | Layout (`get_fields_layout`, `get_sidepanel_sections`) | `handle_perm_level_restrictions` marks hidden fields `hidden=1` |
| 10 | Frontend `checkMandatory` | Treat hidden fields as hidden so SE can still move a lead to C4 (server value is intact) |

## Admin API

- `crm.permissions.field_visibility.get_restricted_field_settings()` — System Manager only. Returns field groups + current rules.
- `crm.permissions.field_visibility.save_restricted_field_rules(rules)` — System Manager only.
- `crm.permissions.field_visibility.get_my_hidden_fields()` — any user. `{doctype: [fieldnames]}` for the frontend.

## Frontend

- `stores/restrictedFields.js` — loads `get_my_hidden_fields` once.
- `utils/fieldTransforms.js` — pure `withHiddenOverrides(overrides, hidden)`; unit-tested.
- `data/document.js` — `checkMandatory` uses it.
- `components/Settings/RestrictedFieldsSettings.vue` — rules editor, under System Configuration, visible to `isAdmin()`.
- `QuoteArea.vue` already hides margin when `null` (`v-if`).

## Tests

Backend `crm/tests/test_restricted_fields.py`:
- no rules → nothing hidden; role rule; user rule; exempt role beats rule; Administrator.
- doc load strips; whole-doc save keeps DB value.
- `list_lead_quote_requests`, `get_quote_revisions`, `get_data` strip.
- activity feed: version skipped, comment masked.
- layout marks hidden.
- non–System Manager can't save rules.

Frontend: `withHiddenOverrides` in `tests/unit/`.

## Known limitations

- Filtering/sorting a list by a hidden field could let a user infer values by probing. Not blocked in v1.
- `/app` desk is not covered (admin-only).
- Frontend caches (meta, layout) are per browser; after a rule change users pick it up on reload.

## Extending later

| Need | Change |
|---|---|
| Hide another field | Add a `FIELD_GROUPS` entry |
| Read-only instead of hidden | Add `mode` column; layout sets `read_only`, save guard already resets |
| Stage-conditional | Add optional `status` column; `get_hidden_fields` takes `doc` |
| Allow-list exceptions | Add `effect` (Hide/Allow) column; user row overrides role row |

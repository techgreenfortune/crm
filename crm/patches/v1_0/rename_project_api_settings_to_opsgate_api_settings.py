"""Rename `CRM Project API Settings` → `CRM OpsGate API Settings` and migrate
the OpsGate base URL from `site_config.json` (`opsgate_api_url`) into the
renamed Single's new `api_base_url` field.

Why: a single admin-configurable source of truth for both Project handoff
and OpsGate SSO. Previously the URL lived in `site_config.json` (CLI-only,
required SSH on every site) while api_key lived in the doctype — two
sources, drift-prone. This consolidates both onto the doctype.

Runs in `pre_model_sync` so the rename happens BEFORE Frappe migrates
doctypes (otherwise migration would create a fresh empty
`CRM OpsGate API Settings` Single and leave the old `CRM Project API
Settings` rows orphaned in tabSingles).
"""

import frappe
from frappe.model.rename_doc import rename_doc


def execute():
	if frappe.db.exists("DocType", "CRM Project API Settings"):
		frappe.flags.ignore_route_conflict_validation = True
		rename_doc("DocType", "CRM Project API Settings", "CRM OpsGate API Settings", force=True)
		frappe.flags.ignore_route_conflict_validation = False

		frappe.reload_doctype("CRM OpsGate API Settings", force=True)

	# Carry forward Password rows from `__Auth` (api_key) — Frappe stores
	# password values keyed by (doctype, name, fieldname), and rename_doc on
	# the DocType does NOT rewrite __Auth.
	if frappe.db.exists("__Auth", {"doctype": "CRM Project API Settings"}):
		Auth = frappe.qb.DocType("__Auth")
		rows = (
			frappe.qb.from_(Auth)
			.select("*")
			.where(Auth.doctype == "CRM Project API Settings")
			.run(as_dict=True)
		)
		for row in rows:
			frappe.qb.into(Auth).insert(
				"CRM OpsGate API Settings",
				"CRM OpsGate API Settings",
				row.fieldname,
				row.password,
				row.encrypted,
			).run()
		frappe.db.sql(
			"DELETE FROM `__Auth` WHERE doctype = %s",
			("CRM Project API Settings",),
		)

	# Migrate the URL from site_config.json into the doctype, but only if the
	# doctype value is empty (don't clobber a value an admin has already set
	# via the UI after the rename ran). Skip on fresh installs where the
	# renamed doctype hasn't been synced yet — post_model_sync will handle it.
	if not frappe.db.exists("DocType", "CRM OpsGate API Settings"):
		return

	conf_url = (frappe.conf.get("opsgate_api_url") or "").strip()
	existing = frappe.db.get_single_value("CRM OpsGate API Settings", "api_base_url") or ""
	if conf_url and not existing:
		frappe.db.set_single_value("CRM OpsGate API Settings", "api_base_url", conf_url)
		frappe.db.commit()

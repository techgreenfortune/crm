"""Drop the deprecated `api_base_url` field from CRM Project API Settings.

The Project API handoff now reads `opsgate_api_url` from `site_config.json`
(same source the SSO flow uses), so the per-site DocType field is dead. This
patch removes the stored value from `tabSingles`. If the stored value differs
from the current `opsgate_api_url`, log it before deleting — that's the only
signal admins get that they had drift between the two configs.
"""

import frappe


def execute():
	# `tabSingles` is a flat (doctype, field, value) table with no `creation`
	# column, so frappe.db.get_value / frappe.db.delete (which auto-add
	# ORDER BY creation) blow up on it. Hit it with raw SQL.
	row = frappe.db.sql(
		"""
		SELECT value FROM `tabSingles`
		WHERE doctype = %s AND field = %s
		""",
		("CRM Project API Settings", "api_base_url"),
	)
	stored = row[0][0] if row else None

	if stored:
		expected = (frappe.conf.get("opsgate_api_url") or "").rstrip("/")
		stored_norm = stored.rstrip("/")
		if expected and stored_norm and stored_norm != expected:
			frappe.log_error(
				f"CRM Project API Settings.api_base_url ({stored!r}) differed from "
				f"site_config.opsgate_api_url ({expected!r}) at deprecation. The "
				f"site_config value is now authoritative for project handoff.",
				"Project API: api_base_url drift at deprecation",
			)

	frappe.db.sql(
		"""
		DELETE FROM `tabSingles`
		WHERE doctype = %s AND field = %s
		""",
		("CRM Project API Settings", "api_base_url"),
	)
	frappe.db.commit()

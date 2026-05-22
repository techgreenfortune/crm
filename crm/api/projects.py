"""Manual project-creation handoff from a Won CRM Lead.

Replaces the old auto-enqueue path (Script 2 After Save on C8) with a button-click
flow at C4 (Won). The button lives on the Lead detail page; this module is its
backend.
"""

from __future__ import annotations

import frappe
from frappe import _


@frappe.whitelist()
def create_project_for_lead(lead: str) -> str:
	"""Trigger external project creation for a Won lead.

	Gates:
	  - Lead must exist; current user is lead_owner OR has Sales Head /
	    Sales Coordinator / System Manager role OR is Administrator.
	  - Lead status must be 'C4' (Won).
	  - Lead lead_status must NOT already be 'Won' (idempotency — already handed off).
	  - For Projects-type leads: `validate_project_specific_fields` must pass
	    (project metadata filled in). Retail-type leads skip that validator —
	    the project API derives `order_type = "retail"` from `custom_lead_type`
	    and only needs the baseline customer/billing fields already enforced by
	    the "CRM Lead — Before Save — Stage Field Requirements" server script
	    at the C4 transition (Won-type + C4 handoff checks).

	On success:
	  - Calls `crm.integrations.project_api.api.create_project_on_won` synchronously.
	  - Sets `lead.lead_status = "Won"` via db.set_value. This path bypasses
	    `CRMLead.validate()` so the `_enforce_c4_won_lock` guard does not fire on
	    the Won write itself. Any future caller that flips to Won via
	    `lead_doc.save()` must also set `lead_doc.flags.ignore_c4_lock = True`.
	  - Posts an [AUTOMATION] Comment on the lead recording the handoff.

	Returns:
	  - The lead's `custom_external_project_id` after creation, OR the existing
	    project id if the lead was already handed off (idempotent re-click).
	"""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead {0} not found").format(lead), frappe.DoesNotExistError)

	lead_doc = frappe.get_doc("CRM Lead", lead)

	# --- Permission ---
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	is_privileged = user == "Administrator" or bool(
		user_roles & {"System Manager", "Sales Head", "Sales Coordinator"}
	)
	if not is_privileged and lead_doc.lead_owner != user:
		frappe.throw(
			_(
				"Only the lead owner (or Sales Head / Sales Coordinator / System Manager) can create the project for this lead."
			),
			frappe.PermissionError,
		)

	# --- Idempotency: external project already created ---
	if lead_doc.get("custom_external_project_id"):
		return lead_doc.custom_external_project_id

	# --- Stage gate ---
	if lead_doc.status != "C4":
		frappe.throw(
			_("Project can only be created for leads at C4 (Won). Current stage: {0}.").format(
				lead_doc.status
			),
			frappe.ValidationError,
		)

	if lead_doc.lead_status == "Won":
		# Defensive — should already be caught by the project_id short-circuit
		# above, but cover the case where Won was set without an id (manual).
		frappe.throw(
			_("This lead is already marked Won. Re-activate it before creating a project."),
			frappe.ValidationError,
		)

	# --- Project-specific fields ---
	# Projects-type leads require the extra project metadata cluster; retail
	# leads skip — the integration handler routes `order_type` off lead_type and
	# already has retail-safe fallbacks for site_address / site_pincode.
	if lead_doc.get("custom_lead_type") == "Projects":
		lead_doc.validate_project_specific_fields()

	# --- Fire the integration handler ---
	from crm.integrations.project_api.api import create_project_on_won

	create_project_on_won(lead_doc.name)

	# Reload to pick up `custom_external_project_id` which the integration sets.
	lead_doc.reload()
	project_id = lead_doc.get("custom_external_project_id") or ""

	# --- Mark the lead Won (C-stage stays at C4; lead_status flips). The
	# `_enforce_c4_won_lock` guard fires on subsequent edits but not on this
	# write — db.set_value bypasses validate().
	frappe.db.set_value("CRM Lead", lead_doc.name, "lead_status", "Won")

	# --- Audit Comment ---
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead_doc.name,
				"content": f"[AUTOMATION] Project created (external id: {project_id or 'pending'}) by {user}. Lead marked Won (handoff complete).",
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Audit is best-effort.
		pass

	return project_id

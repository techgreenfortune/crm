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

	# --- Idempotency: external project + draft order already created ---
	# Only short-circuit when BOTH ids are set — a lead with a project but no
	# draft order (interrupted prior handoff) must keep re-firing so it can
	# reach `create_project_on_won`'s hard-gate and OpsGate's self-heal path,
	# instead of silently returning as if the handoff had fully succeeded.
	if lead_doc.get("custom_external_project_id") and lead_doc.get("custom_external_order_id"):
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

	# --- Affiliate-commission approval gate ---
	# When the lead is tagged as an affiliate lead, the commission must be
	# explicitly approved by a Sales Head before OpsGate is fired.  Any change
	# to the commission % / affiliate / toggle resets approval back to empty,
	# so the gate effectively blocks creation under any "unapproved" condition.
	if lead_doc.get("custom_is_affiliate_lead"):
		approval_status = lead_doc.get("custom_affiliate_approval_status")
		if approval_status != "Approved":
			if approval_status == "Pending Approval":
				msg = _(
					"Affiliate commission is awaiting Sales Head approval. "
					"Project creation is blocked until approval is granted."
				)
			elif approval_status == "Rejected":
				msg = _(
					"Affiliate commission was rejected.  Revise the commission and re-submit "
					"for approval before creating the project."
				)
			else:
				msg = _(
					"Affiliate commission must be approved by a Sales Head before "
					"the project can be created.  Click Submit for Approval first."
				)
			frappe.throw(msg, title=_("Affiliate Approval Required"))

	# --- Project-specific fields ---
	# Projects-type leads require the extra project metadata cluster; retail
	# leads skip — the integration handler routes `order_type` off lead_type and
	# already has retail-safe fallbacks for site_address / site_pincode.
	if lead_doc.get("custom_lead_type") == "Projects":
		lead_doc.validate_project_specific_fields()

	# --- Fire the integration handler ---
	# Raises (and never returns) if the project id or the draft order id is
	# missing from OpsGate's response — see `create_project_on_won`'s hard
	# gates. So reaching the code below means both ids are confirmed set.
	from crm.integrations.project_api.api import create_project_on_won

	create_project_on_won(lead_doc.name)

	# Reload to pick up `custom_external_project_id` which the integration sets.
	lead_doc.reload()
	project_id = lead_doc.get("custom_external_project_id") or ""

	# Hard gate: if the integration didn't populate the project id, the handoff
	# did NOT succeed end-to-end (HTTP error logged earlier, or 2xx with a body
	# shape we couldn't read). Refuse to flip lead_status to Won — leave the
	# lead re-triable. Post a failure audit + throw so the user sees the issue
	# at click time instead of finding a half-handed-off lead later.
	if not project_id:
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_doc.name,
					"content": (
						f"[AUTOMATION] Project creation FAILED for this lead "
						f"(no external project id returned). Triggered by {user}. "
						f"Check Error Log for details and re-click Create Project."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass
		frappe.throw(
			_(
				"Project creation did not return a project id. The handoff failed — "
				"check Error Log for details. The lead is NOT marked Won; you can "
				"retry once the underlying issue is resolved."
			),
			title=_("Project Handoff Failed"),
		)

	# --- Mark the lead Won (C-stage stays at C4; lead_status flips). The
	# `_enforce_c4_won_lock` guard fires on subsequent edits but not on this
	# write — db.set_value bypasses validate().
	frappe.db.set_value("CRM Lead", lead_doc.name, "lead_status", "Won")

	# --- Fire Lead Won notification from Python (after project confirmed + Won set).
	# Notification is disabled in fixtures; send() bypasses enabled/condition checks.
	lead_doc.lead_status = "Won"  # reflect in-memory for .send()
	try:
		frappe.get_doc("Notification", "Lead Won").send(lead_doc)
	except Exception:
		frappe.log_error(title="Lead Won notification failed", message=frappe.get_traceback())

	# --- Audit Comment ---
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead_doc.name,
				"content": f"[AUTOMATION] Project created (external id: {project_id}) by {user}. Lead marked Won (handoff complete).",
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Audit is best-effort.
		pass

	return project_id

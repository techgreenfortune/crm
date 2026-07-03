"""Brevo template-email triggers for the affiliate commission approval flow.

Mirrors the registry pattern in ``quote_emails.py`` and ``task_emails.py``.
Three triggers cover the lifecycle:

1. ``affiliate_approval_request``  — sales user submits → email to the chosen
   Sales Head asking for approval
2. ``affiliate_approval_granted``  — Sales Head approves → email to the lead
   owner confirming approval (next step: Create Project)
3. ``affiliate_approval_rejected`` — Sales Head rejects → email to the lead
   owner with the remarks so they can revise + resubmit

All sends are enqueued via ``frappe.enqueue`` so the originating API call
returns immediately.  Failures are swallowed and logged.

Add the trigger keys to ``BREVO_TEMPLATES`` in ``template_config.py`` with
the matching Brevo template IDs.  Until a template ID is set, the dispatcher
silently no-ops — safe to ship the wiring before the templates are designed.
"""

from __future__ import annotations

import frappe

from crm.integrations.brevo.brevo_handler import is_brevo_enabled, send_template_email
from crm.integrations.brevo.template_config import BREVO_TEMPLATES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_email(user_name: str | None) -> str | None:
	if not user_name:
		return None
	email = frappe.db.get_value("User", user_name, "email") or user_name
	return email if "@" in (email or "") else None


def _user_full_name(user_name: str | None) -> str:
	if not user_name:
		return ""
	return frappe.db.get_value("User", user_name, "full_name") or user_name


def _common_params(lead_doc) -> dict:
	"""Variables every affiliate template can reference."""
	affiliate_name = lead_doc.get("custom_affiliate")
	affiliate_label = (
		(frappe.db.get_value("CRM Affiliate", affiliate_name, "affiliate_name") if affiliate_name else "")
		or affiliate_name
		or ""
	)
	commission_pct = float(lead_doc.get("custom_affiliate_commission_pct") or 0)
	tentative_value = float(lead_doc.get("custom_tentative_value") or 0)
	commission_amount = round(tentative_value * commission_pct / 100.0, 2) if commission_pct else 0.0

	# Emails always show monetary / percentage values to exactly 2 decimal
	# places.  Format as string here — JSON would otherwise drop trailing
	# zeros (e.g. 25.0 instead of 25.00) and the template can't reformat.
	return {
		"lead_id": lead_doc.name,
		"customer_name": lead_doc.get("lead_name") or "",
		"affiliate_id": affiliate_name or "",
		"affiliate_name": affiliate_label,
		"commission_pct": f"{commission_pct:.2f}",
		"tentative_value": f"{tentative_value:.2f}",
		"commission_amount": f"{commission_amount:.2f}",
		"lead_owner_email": lead_doc.get("lead_owner") or "",
		"lead_owner_name": _user_full_name(lead_doc.get("lead_owner")),
	}


# ---------------------------------------------------------------------------
# Recipient + params per trigger
# ---------------------------------------------------------------------------


def _params_approval_request(lead_doc) -> dict:
	p = _common_params(lead_doc)
	p["submitted_by_email"] = lead_doc.get("custom_affiliate_submitted_by") or ""
	p["submitted_by_name"] = _user_full_name(lead_doc.get("custom_affiliate_submitted_by"))
	p["submitted_to_email"] = lead_doc.get("custom_affiliate_submitted_to") or ""
	p["submitted_to_name"] = _user_full_name(lead_doc.get("custom_affiliate_submitted_to"))
	return p


def _recipient_approval_request(lead_doc) -> dict | None:
	"""TO: the Sales Head the request was submitted to.  No CC."""
	email = _user_email(lead_doc.get("custom_affiliate_submitted_to"))
	if not email:
		return None
	return {"to": email, "cc": []}


def _params_approval_done(lead_doc) -> dict:
	p = _common_params(lead_doc)
	p["action_by_email"] = lead_doc.get("custom_affiliate_approved_by") or ""
	p["action_by_name"] = _user_full_name(lead_doc.get("custom_affiliate_approved_by"))
	p["remarks"] = lead_doc.get("custom_affiliate_approval_remarks") or ""
	return p


def _recipient_lead_owner(lead_doc) -> dict | None:
	"""TO: the lead's owner (sales user who submitted).  No CC."""
	email = _user_email(lead_doc.get("lead_owner"))
	if not email:
		return None
	return {"to": email, "cc": []}


# ---------------------------------------------------------------------------
# Trigger registry
# ---------------------------------------------------------------------------

TRIGGERS: dict[str, dict] = {
	"affiliate_approval_request": {
		"recipient": _recipient_approval_request,
		"params": _params_approval_request,
	},
	"affiliate_approval_granted": {
		"recipient": _recipient_lead_owner,
		"params": _params_approval_done,
	},
	"affiliate_approval_rejected": {
		"recipient": _recipient_lead_owner,
		"params": _params_approval_done,
	},
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_affiliate_email(trigger: str, lead_name: str) -> None:
	"""Send the Brevo template email registered under ``trigger`` for the lead.

	Runs in a worker via ``frappe.enqueue``; failures are swallowed + logged.
	Whitelisted because the dispatcher is invoked from server scripts in some
	flows (same pattern as ``send_task_email``).
	"""
	try:
		if not is_brevo_enabled():
			return

		spec = TRIGGERS.get(trigger)
		if not spec:
			frappe.log_error(
				title="Brevo Affiliate Email — unknown trigger",
				message=f"trigger={trigger} lead={lead_name}",
			)
			return

		template_id = BREVO_TEMPLATES.get(trigger) or 0
		if not template_id:
			# Soft skip — lets you ship the wiring before the template is
			# designed in Brevo.
			return

		lead_doc = frappe.get_doc("CRM Lead", lead_name)
		recipient = spec["recipient"](lead_doc)
		if not recipient or not recipient.get("to"):
			frappe.log_error(
				title="Brevo Affiliate Email — no recipient",
				message=f"trigger={trigger} lead={lead_name}",
			)
			return

		params = spec["params"](lead_doc)
		send_template_email(
			template_id=template_id,
			recipients=recipient["to"],
			params=params,
			cc=recipient.get("cc") or None,
		)
	except Exception:
		frappe.log_error(
			title=f"Brevo Affiliate Email failed ({trigger})",
			message=frappe.get_traceback(),
		)


def _enqueue(trigger: str, lead_name: str) -> None:
	"""Push the trigger to the default RQ queue.  Non-blocking."""
	frappe.enqueue(
		"crm.integrations.brevo.affiliate_emails.send_affiliate_email",
		queue="default",
		trigger=trigger,
		lead_name=lead_name,
	)


def fire_affiliate_email(trigger: str, lead_name: str) -> None:
	"""Public entry point used by crm.api.affiliate handlers."""
	_enqueue(trigger, lead_name)

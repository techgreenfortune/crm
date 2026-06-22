"""Brevo template-email triggers for the CRM Quote Request workflow.

Architecture
------------
- All wiring goes through a single ``TRIGGERS`` registry.  Adding a new email
  for a future workflow event means: (a) add a template ID in
  ``template_config.py``, (b) add an entry to ``TRIGGERS``, and (c) write one
  ``_params_*`` builder.  No other file changes needed.
- Template IDs live in ``template_config.py`` as constants — change-deploy-
  restart to update.
- Estimation-team recipients are resolved at send time from active users
  with the ``Estimation Team`` role.  The first (alphabetically by username)
  is the primary ``to`` recipient; the rest are added as ``cc``.  No
  site_config or hard-coded address required — manage membership via roles.
- A trigger silently no-ops if Brevo is disabled, the template ID is 0/missing,
  or the recipient cannot be resolved.  This lets you ship the wiring before
  every template is designed in Brevo.
- Sends run through ``frappe.enqueue`` so a slow Brevo API call never blocks
  a doc save.
"""

from __future__ import annotations

import base64

import frappe
from frappe.utils import get_url

from crm.integrations.brevo.brevo_handler import is_brevo_enabled, send_template_email
from crm.integrations.brevo.template_config import BREVO_TEMPLATES, STATIC_CCS

# Max attachment size we'll embed inline (Brevo limit is ~10 MB total).
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# Recipient resolvers
# ---------------------------------------------------------------------------


def _user_email(user_name: str | None) -> str | None:
	"""Resolve an active User's email.  Falls back to User.name when it is
	itself an email-format login (Frappe's default for self-registered users).
	"""
	if not user_name:
		return None
	email = frappe.db.get_value("User", user_name, "email") or user_name
	return email if "@" in (email or "") else None


def _estimation_recipient(qr_doc) -> dict | None:
	"""Resolve recipients from active users with the ``Estimation Team`` role.

	Returns ``{"to": <first email>, "cc": [<rest>]}`` or ``None`` if no active
	user has the role.  Ordering is alphabetical by User.name so the "primary"
	is deterministic — change membership by adding/removing the role on the
	user, not by tweaking config.
	"""
	role_rows = frappe.db.get_all(
		"Has Role",
		filters={"role": "Estimation Team", "parenttype": "User"},
		fields=["parent"],
	)
	if not role_rows:
		return None

	users = sorted({r["parent"] for r in role_rows})
	active = set(
		frappe.db.get_all(
			"User",
			filters={"enabled": 1, "name": ["in", users]},
			pluck="name",
		)
	)
	emails = [e for u in users if u in active for e in [_user_email(u)] if e]
	if not emails:
		return None

	return {"to": emails[0], "cc": emails[1:]}


def _lead_owner_recipient(qr_doc) -> dict | None:
	"""Sales user — the ``lead_owner`` email on the parent lead.  No CCs."""
	email = _user_email(qr_doc.get("lead_owner"))
	if not email:
		return None
	return {"to": email, "cc": []}


# ---------------------------------------------------------------------------
# Params builders (one per trigger)
# ---------------------------------------------------------------------------


def _lead_fields(qr_doc) -> dict:
	"""Common lead-side fields included as context in every template."""
	lead = (
		frappe.db.get_value(
			"CRM Lead",
			qr_doc.lead,
			[
				"lead_name",
				"custom_tentative_value",
				"custom_tentative_area_sqft",
				"custom_tentative_units",
			],
			as_dict=True,
		)
		or {}
	)
	return {
		"customer_name": lead.get("lead_name") or "",
		"lead_id": qr_doc.lead,
		"tentative_value": lead.get("custom_tentative_value") or 0,
		"tentative_sft": lead.get("custom_tentative_area_sqft") or 0,
		"tentative_units": lead.get("custom_tentative_units") or 0,
	}


def _quote_file_url(qr_doc) -> str:
	path = qr_doc.get("quote_file")
	return get_url(path) if path else ""


def _quote_file_attachment(qr_doc) -> list | None:
	"""Read the QR's quote_file from disk and return Brevo's attachment payload.

	Returns ``None`` when there is no file, the File record is missing, the file
	is unreadable, or it exceeds the size cap.  Failures are logged and
	swallowed so the email still sends without the attachment.
	"""
	file_url = qr_doc.get("quote_file")
	if not file_url:
		return None
	try:
		file_doc = frappe.get_doc("File", {"file_url": file_url})
		content = file_doc.get_content()  # returns bytes for binary files
		if isinstance(content, str):
			content = content.encode("utf-8")
		if not content:
			return None
		if len(content) > _MAX_ATTACHMENT_BYTES:
			frappe.log_error(
				title="Brevo Quote Email — attachment too large",
				message=f"qr={qr_doc.name} file={file_url} size={len(content)}",
			)
			return None
		return [
			{
				"name": file_doc.file_name or "quotation.pdf",
				"content": base64.b64encode(content).decode("ascii"),
			}
		]
	except Exception:
		frappe.log_error(
			title="Brevo Quote Email — attachment fetch failed",
			message=frappe.get_traceback(),
		)
		return None


def _params_quote_requested(qr_doc) -> dict:
	# Fields requested: Customer name, Lead ID, tentative value, tentative sft, tentative units
	base = _lead_fields(qr_doc)
	base["qr_id"] = qr_doc.name
	return base


def _params_quote_received(qr_doc) -> dict:
	# Fields requested: Quotation Value, Quotation Sft, Quantity, Quotation Number,
	# Quotation File, Estimation team remarks, Sales user name
	owner = qr_doc.get("lead_owner")
	sales_user_name = (frappe.db.get_value("User", owner, "full_name") if owner else "") or ""
	return {
		**_lead_fields(qr_doc),
		"qr_id": qr_doc.name,
		"sales_user_name": sales_user_name,
		"quote_value": qr_doc.get("quote_value") or 0,
		"quote_sft": qr_doc.get("quote_sq_ft") or 0,
		"quantity": qr_doc.get("total_quantity") or 0,
		"quote_number": qr_doc.get("quote_number") or "",
		"quote_file": "Attached",
		"estimation_remarks": qr_doc.get("estimation_remarks") or "",
	}


def _params_revision_requested(qr_doc) -> dict:
	# Fields requested: Quotation Value, Quotation Sft, Quantity, Quotation Number,
	# Quotation File, revision remarks
	owner = qr_doc.get("lead_owner")
	sales_user_name = (frappe.db.get_value("User", owner, "full_name") if owner else "") or ""
	return {
		**_lead_fields(qr_doc),
		"qr_id": qr_doc.name,
		"sales_user_name": sales_user_name,
		"quote_value": qr_doc.get("quote_value") or 0,
		"quote_sft": qr_doc.get("quote_sq_ft") or 0,
		"quantity": qr_doc.get("total_quantity") or 0,
		"quote_number": qr_doc.get("quote_number") or "",
		"quote_file": "Attached",
		"revision_remarks": qr_doc.get("notes") or "",
	}


def _params_quote_accepted(qr_doc) -> dict:
	# Fields requested: Quotation Value, Quotation Sft, Quantity, Quotation Number, Quotation File
	return {
		**_lead_fields(qr_doc),
		"qr_id": qr_doc.name,
		"quote_value": qr_doc.get("quote_value") or 0,
		"quote_sft": qr_doc.get("quote_sq_ft") or 0,
		"quantity": qr_doc.get("total_quantity") or 0,
		"quote_number": qr_doc.get("quote_number") or "",
		"quote_file": "Attached",
	}


# ---------------------------------------------------------------------------
# Activity-log summary builders — one per trigger.  Returns the HTML body
# of the Comment that gets pinned to the parent lead's timeline.
# ---------------------------------------------------------------------------


def _fmt_money(value) -> str:
	try:
		from frappe.utils import fmt_money

		return fmt_money(value or 0, currency="INR")
	except Exception:
		return str(value or 0)


def _fmt_recipient(recipient) -> str:
	"""Render a recipient dict as ``to@x.com`` or ``to@x.com (+N in CC)`` for
	the activity-log summary.  Tolerates legacy string input.
	"""
	if isinstance(recipient, dict):
		to = recipient.get("to") or ""
		cc = recipient.get("cc") or []
		if cc:
			return f"{to} (+{len(cc)} in CC)"
		return to
	return str(recipient or "")


def _summary_quote_requested(qr_doc, recipient, params) -> str:
	return (
		f"<b>[Email Sent]</b> Quotation request emailed to Estimation Team "
		f"(<b>{_fmt_recipient(recipient)}</b>).<br>"
		f"Tentative Value: <b>{_fmt_money(params.get('tentative_value'))}</b> · "
		f"SFT: <b>{params.get('tentative_sft') or 0}</b> · "
		f"Units: <b>{params.get('tentative_units') or 0}</b><br>"
		f"Quote Request: {qr_doc.name}"
	)


def _summary_quote_received(qr_doc, recipient, params) -> str:
	remarks = params.get("estimation_remarks") or "—"
	return (
		f"<b>[Email Sent]</b> Quotation uploaded — emailed to lead owner "
		f"(<b>{_fmt_recipient(recipient)}</b>) with PDF attached.<br>"
		f"Quote <b>{params.get('quote_number') or qr_doc.name}</b> · "
		f"Value: <b>{_fmt_money(params.get('quote_value'))}</b> · "
		f"SFT: <b>{params.get('quote_sft') or 0}</b> · "
		f"Qty: <b>{params.get('quantity') or 0}</b><br>"
		f"Estimation Remarks: {remarks}"
	)


def _summary_revision_requested(qr_doc, recipient, params) -> str:
	remarks = params.get("revision_remarks") or "—"
	return (
		f"<b>[Email Sent]</b> Revision request emailed to Estimation Team "
		f"(<b>{_fmt_recipient(recipient)}</b>) with PDF attached.<br>"
		f"Quote <b>{params.get('quote_number') or qr_doc.name}</b> · "
		f"Value: <b>{_fmt_money(params.get('quote_value'))}</b> · "
		f"SFT: <b>{params.get('quote_sft') or 0}</b> · "
		f"Qty: <b>{params.get('quantity') or 0}</b><br>"
		f"Revision Remarks: {remarks}"
	)


def _summary_quote_accepted(qr_doc, recipient, params) -> str:
	return (
		f"<b>[Email Sent]</b> Quotation accepted — emailed to Estimation Team "
		f"(<b>{_fmt_recipient(recipient)}</b>) with PDF attached.<br>"
		f"Quote <b>{params.get('quote_number') or qr_doc.name}</b> · "
		f"Value: <b>{_fmt_money(params.get('quote_value'))}</b> · "
		f"SFT: <b>{params.get('quote_sft') or 0}</b> · "
		f"Qty: <b>{params.get('quantity') or 0}</b>"
	)


def _log_email_to_lead(qr_doc, summary_html: str) -> None:
	"""Pin a Comment to the parent CRM Lead's activity timeline.

	Uses the same ``comment_type='Comment'`` pattern as the existing transition-
	trail server script so this entry renders identically in the Activities tab.
	Failures are swallowed — the email already went out; a missing audit comment
	must not break the worker.
	"""
	try:
		if not qr_doc.get("lead"):
			return
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": qr_doc.lead,
				"content": summary_html,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="Brevo Quote Email — activity log failed",
			message=frappe.get_traceback(),
		)


# ---------------------------------------------------------------------------
# Trigger registry — single source of truth.  Add new triggers here.
# ---------------------------------------------------------------------------

TRIGGERS: dict[str, dict] = {
	"quote_requested": {
		"recipient": _estimation_recipient,
		"params": _params_quote_requested,
		"attach_quote_file": False,
		"log_summary": _summary_quote_requested,
	},
	"quote_received": {
		"recipient": _lead_owner_recipient,
		"params": _params_quote_received,
		"attach_quote_file": True,
		"log_summary": _summary_quote_received,
	},
	"revision_requested": {
		"recipient": _estimation_recipient,
		"params": _params_revision_requested,
		"attach_quote_file": True,
		"log_summary": _summary_revision_requested,
	},
	"quote_accepted": {
		"recipient": _estimation_recipient,
		"params": _params_quote_accepted,
		"attach_quote_file": True,
		"log_summary": _summary_quote_accepted,
	},
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def send_quote_email(trigger: str, qr_name: str) -> None:
	"""Send the Brevo template email registered under ``trigger`` for the QR.

	Designed to be invoked via ``frappe.enqueue`` so failures (network, missing
	template, etc.) never block the originating doc save.  All exceptions are
	logged via ``frappe.log_error`` and swallowed.
	"""
	try:
		if not is_brevo_enabled():
			return

		spec = TRIGGERS.get(trigger)
		if not spec:
			frappe.log_error(
				title="Brevo Quote Email — unknown trigger",
				message=f"trigger={trigger} qr={qr_name}",
			)
			return

		template_id = BREVO_TEMPLATES.get(trigger) or 0
		if not template_id:
			# Soft skip — lets you enable triggers one at a time by filling
			# in template IDs in template_config.py as they are designed in Brevo.
			return

		qr_doc = frappe.get_doc("CRM Quote Request", qr_name)
		recipient = spec["recipient"](qr_doc)
		if not recipient or not recipient.get("to"):
			frappe.log_error(
				title="Brevo Quote Email — no recipient",
				message=f"trigger={trigger} qr={qr_name}",
			)
			return

		to_email = recipient["to"]
		cc_emails = list(recipient.get("cc") or [])
		for extra in STATIC_CCS.get(trigger, []):
			if extra and extra != to_email and extra not in cc_emails:
				cc_emails.append(extra)

		params = spec["params"](qr_doc)
		attachments = _quote_file_attachment(qr_doc) if spec.get("attach_quote_file") else None

		send_template_email(
			template_id=template_id,
			recipients=to_email,
			params=params,
			attachments=attachments,
			cc=cc_emails,
		)

		# Pin a human-readable summary to the parent lead's Activities timeline.
		# Only runs if the send didn't raise — so failed emails don't get logged
		# as "sent" in the audit trail.
		summary_builder = spec.get("log_summary")
		if summary_builder:
			_log_email_to_lead(qr_doc, summary_builder(qr_doc, recipient, params))
	except Exception:
		frappe.log_error(
			title=f"Brevo Quote Email failed ({trigger})",
			message=frappe.get_traceback(),
		)


def _enqueue(trigger: str, qr_name: str) -> None:
	"""Push a trigger into the default queue.  Non-blocking."""
	frappe.enqueue(
		"crm.integrations.brevo.quote_emails.send_quote_email",
		queue="default",
		trigger=trigger,
		qr_name=qr_name,
	)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def fire_quote_requested(qr_name: str) -> None:
	"""Called from the request_quote() API after a Pending QR is created."""
	_enqueue("quote_requested", qr_name)


def on_quote_request_update(doc, method=None) -> None:
	"""doc_events hook — dispatches status-change emails.

	Only fires when ``status`` actually transitioned (not on every save).
	"""
	try:
		if not doc.has_value_changed("status"):
			return

		status = doc.get("status")
		if status == "Quote Received":
			_enqueue("quote_received", doc.name)
		elif status == "Revision Requested":
			_enqueue("revision_requested", doc.name)
		elif status == "Accepted":
			_enqueue("quote_accepted", doc.name)
	except Exception:
		frappe.log_error(
			title="Brevo Quote Email — on_update dispatch failed",
			message=frappe.get_traceback(),
		)

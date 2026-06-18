"""Brevo template-email trigger for CRM Task reminders.

Mirrors the registry pattern in ``quote_emails.py`` so adding more task-side
triggers (e.g. overdue, reassigned) later is a one-entry change.

Two entry points wire the only current trigger (``task_reminder``):

1. ``crm.api.tasks.send_due_today_reminder`` — doc_event on CRM Task save.
   Fires the email immediately when a task is created/updated with
   ``due_date`` matching today (and the reminder hasn't been sent yet).

2. ``crm.api.tasks.send_due_today_reminders_batch`` — daily cron at 21:00.
   Catches tasks created on earlier days whose due_date arrives today,
   so they get a reminder even if no save touched them today.

Both paths call ``fire_task_reminder(task_name)`` which enqueues the
template-email send via ``frappe.enqueue`` so a slow Brevo API never
blocks the originating save or cron tick.

After a successful send, a Comment is pinned to the task's parent
document (``reference_doctype`` / ``reference_docname`` — typically
CRM Lead or CRM Deal) so the lead's Activities timeline reflects the
reminder.
"""

from __future__ import annotations

import frappe

from crm.integrations.brevo.brevo_handler import is_brevo_enabled, send_template_email
from crm.integrations.brevo.template_config import BREVO_TEMPLATES

# ---------------------------------------------------------------------------
# Recipient resolver
# ---------------------------------------------------------------------------


def _user_email(user_name: str | None) -> str | None:
	"""Resolve an active User's email; fall back to the User.name when it is
	itself an email-format login (Frappe's default for self-registered users).
	"""
	if not user_name:
		return None
	email = frappe.db.get_value("User", user_name, "email") or user_name
	return email if "@" in (email or "") else None


def _parent_owner_email(task_doc) -> str | None:
	"""Resolve the owner email of the task's parent doc (lead or deal).

	- ``CRM Lead`` → ``lead_owner``
	- ``CRM Deal`` → ``deal_owner``
	- anything else / no reference → ``None``
	"""
	ref_dt = task_doc.get("reference_doctype")
	ref_dn = task_doc.get("reference_docname")
	if not (ref_dt and ref_dn):
		return None
	owner_field = {"CRM Lead": "lead_owner", "CRM Deal": "deal_owner"}.get(ref_dt)
	if not owner_field:
		return None
	owner_user = frappe.db.get_value(ref_dt, ref_dn, owner_field)
	return _user_email(owner_user)


def _task_recipient(task_doc) -> dict | None:
	"""Reminder goes to ``assigned_to`` (TO) + the linked lead/deal owner (CC).

	Returns ``{"to": email, "cc": [...]}`` or ``None`` if no valid recipient.

	Lead-owner CC is skipped when it matches the TO (avoid double-sending) or
	when the task has no linked parent.
	"""
	to_email = _user_email(task_doc.get("assigned_to"))
	if not to_email:
		return None

	cc_emails: list[str] = []
	owner_email = _parent_owner_email(task_doc)
	if owner_email and owner_email != to_email:
		cc_emails.append(owner_email)

	return {"to": to_email, "cc": cc_emails}


# ---------------------------------------------------------------------------
# Params builder
# ---------------------------------------------------------------------------


def _lead_name_for_task(task_doc) -> str:
	"""Best-effort fetch of the parent lead's display name."""
	ref_dt = task_doc.get("reference_doctype")
	ref_dn = task_doc.get("reference_docname")
	if not (ref_dt and ref_dn):
		return ""
	if ref_dt == "CRM Lead":
		return frappe.db.get_value("CRM Lead", ref_dn, "lead_name") or ref_dn
	if ref_dt == "CRM Deal":
		return frappe.db.get_value("CRM Deal", ref_dn, "deal_name") or ref_dn
	return ref_dn


def _html_to_plain_text(html: str) -> str:
	"""Convert rich-editor HTML to readable plain text for a plain-text Brevo template.

	- Block-level tags (``</p>``, ``</div>``, ``</li>``, ``</h*>``, ``<br>``) become
	  newlines so paragraphs survive.
	- ``<img>`` tags are stripped — the ``/files/`` URLs they use are private and
	  unreachable from external mailboxes; including them as raw HTML would also
	  break the plain-text template (which is what was happening before).
	- Common HTML entities are decoded.
	"""
	import re

	if not html:
		return ""

	text = html
	text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
	text = re.sub(r"</p>|</div>|</li>|</h\d>", "\n", text, flags=re.IGNORECASE)
	text = re.sub(r"<img[^>]*>", "", text, flags=re.IGNORECASE)
	text = re.sub(r"<[^>]+>", "", text)
	text = (
		text.replace("&amp;", "&")
		.replace("&lt;", "<")
		.replace("&gt;", ">")
		.replace("&nbsp;", " ")
		.replace("&quot;", '"')
		.replace("&#39;", "'")
	)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


def _params_task_reminder(task_doc) -> dict:
	assigned_to = task_doc.get("assigned_to")
	assigned_to_name = (frappe.db.get_value("User", assigned_to, "full_name") if assigned_to else "") or ""

	due_date = task_doc.get("due_date")
	due_date_str = frappe.utils.format_datetime(due_date) if due_date else ""

	return {
		"task_id": task_doc.name,
		"task_title": task_doc.get("title") or "",
		"priority": task_doc.get("priority") or "",
		"status": task_doc.get("status") or "",
		"due_date": due_date_str,
		# Plain-text description — rich-editor HTML is stripped so it doesn't
		# render as raw markup inside a plain-text Brevo template.  If you ever
		# switch the template to HTML mode and want the original markup, use
		# task_doc.get("description") raw (or add a separate `description_html`
		# param here).
		"description": _html_to_plain_text(task_doc.get("description") or ""),
		"assigned_to_email": assigned_to or "",
		"assigned_to_name": assigned_to_name,
		"reference_doctype": task_doc.get("reference_doctype") or "",
		"reference_name": task_doc.get("reference_docname") or "",
		"lead_name": _lead_name_for_task(task_doc),
	}


# ---------------------------------------------------------------------------
# Activity log summary
# ---------------------------------------------------------------------------


def _summary_task_reminder(task_doc, recipient: dict, params: dict) -> str:
	due = params.get("due_date") or "—"
	priority = params.get("priority") or "—"
	to = (recipient or {}).get("to") or ""
	cc_list = (recipient or {}).get("cc") or []
	cc_suffix = f" · CC: <b>{', '.join(cc_list)}</b>" if cc_list else ""
	return (
		f"<b>[Email Sent]</b> Task reminder emailed to <b>{to}</b>{cc_suffix}.<br>"
		f"Task: <b>{params.get('task_title') or task_doc.name}</b><br>"
		f"Due: <b>{due}</b> · Priority: <b>{priority}</b>"
	)


def _log_reminder_to_parent(task_doc, summary_html: str) -> None:
	"""Pin a Comment to the task's parent (CRM Lead / CRM Deal) timeline.

	Mirrors the existing ``_log_email_to_lead`` pattern in ``quote_emails.py``
	so reminders render alongside other automation comments.  Failures are
	swallowed — the email already went out; a missing audit comment must
	not break the worker.
	"""
	ref_dt = task_doc.get("reference_doctype")
	ref_dn = task_doc.get("reference_docname")
	if not (ref_dt and ref_dn):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": ref_dt,
				"reference_name": ref_dn,
				"content": summary_html,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="Brevo Task Email — activity log failed",
			message=frappe.get_traceback(),
		)


# ---------------------------------------------------------------------------
# Trigger registry — single source of truth.  Add new triggers here.
# ---------------------------------------------------------------------------

TRIGGERS: dict[str, dict] = {
	"task_reminder": {
		"recipient": _task_recipient,
		"params": _params_task_reminder,
		"log_summary": _summary_task_reminder,
	},
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_task_email(trigger: str, task_name: str) -> None:
	"""Send the Brevo template email registered under ``trigger`` for the task.

	Runs in a worker via ``frappe.enqueue``; failures are swallowed + logged.

	Whitelisted so the catch-up cron Server Script can enqueue it.  Server
	Scripts run inside Frappe's ``safe_exec`` sandbox, which routes
	``frappe.enqueue(...)`` through ``call_whitelisted_function`` — that
	gatekeeper requires the target to carry ``@frappe.whitelist()``.
	Without the decorator, the enqueued job throws PermissionError in the
	worker (visible as "Function ... is not whitelisted" in Error Log).
	"""
	try:
		if not is_brevo_enabled():
			return

		spec = TRIGGERS.get(trigger)
		if not spec:
			frappe.log_error(
				title="Brevo Task Email — unknown trigger",
				message=f"trigger={trigger} task={task_name}",
			)
			return

		template_id = BREVO_TEMPLATES.get(trigger) or 0
		if not template_id:
			# Soft skip — lets you ship the wiring before the template is
			# designed in Brevo.  Fill in the ID in template_config.py to enable.
			return

		task_doc = frappe.get_doc("CRM Task", task_name)
		recipient = spec["recipient"](task_doc)
		if not recipient or not recipient.get("to"):
			frappe.log_error(
				title="Brevo Task Email — no recipient",
				message=f"trigger={trigger} task={task_name}",
			)
			return

		params = spec["params"](task_doc)
		send_template_email(
			template_id=template_id,
			recipients=recipient["to"],
			params=params,
			cc=recipient.get("cc") or None,
		)

		summary_builder = spec.get("log_summary")
		if summary_builder:
			_log_reminder_to_parent(task_doc, summary_builder(task_doc, recipient, params))
	except Exception:
		frappe.log_error(
			title=f"Brevo Task Email failed ({trigger})",
			message=frappe.get_traceback(),
		)


def _enqueue(trigger: str, task_name: str) -> None:
	"""Push the trigger to the default RQ queue.  Non-blocking."""
	frappe.enqueue(
		"crm.integrations.brevo.task_emails.send_task_email",
		queue="default",
		trigger=trigger,
		task_name=task_name,
	)


# ---------------------------------------------------------------------------
# Public entry point (called from crm.api.tasks)
# ---------------------------------------------------------------------------


def fire_task_reminder(task_name: str) -> None:
	"""Enqueue a task_reminder email for the given task."""
	_enqueue("task_reminder", task_name)

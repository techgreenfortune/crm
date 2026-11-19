"""Track the last time a person did real sales work on a CRM Lead.

User activity = calls, C-stage (status) changes, comments, notes and quote
events. Data corrections and owner reassignment don't count, and neither does
automation. Missed incoming calls don't count either: nobody did anything.
frontend/src/utils/activityCategory.js applies the same split to the Activity
timeline's User filter — keep the two in sync.

Administrator counts as a person, so stage changes run as Administrator from
bench console or scripts also stamp the lead.
"""

import re

import frappe
from frappe.utils import get_datetime, now_datetime

FIELD = "custom_last_user_activity_on"
SYSTEM_USERS = frozenset({"Guest"})
AUTOMATION_PREFIX = "[AUTOMATION]"
# Quote comments carry the automation prefix but record a person's action
# (request, upload, revision, accept), so they count as user activity.
USER_TRIGGERED_AUTOMATION_PREFIX = "[AUTOMATION] Quote"

_LEADING_TAGS = re.compile(r"^\s*(?:<[^>]+>\s*)*")


def is_human(user: str | None) -> bool:
	return bool(user) and user not in SYSTEM_USERS


def is_automated_comment(content: str | None) -> bool:
	text = _LEADING_TAGS.sub("", content or "")
	return text.startswith(AUTOMATION_PREFIX) and not text.startswith(USER_TRIGGERED_AUTOMATION_PREFIX)


def _skip() -> bool:
	flags = frappe.flags
	return bool(flags.in_import or flags.in_install or flags.in_migrate or flags.in_patch)


def touch(lead: str | None, at=None) -> None:
	"""Move the lead's last-user-activity stamp forward (never backward)."""
	if not lead:
		return
	at = get_datetime(at) if at else now_datetime()
	current = frappe.db.get_value("CRM Lead", lead, FIELD)
	if current is None and not frappe.db.exists("CRM Lead", lead):
		return
	if current and get_datetime(current) >= at:
		return
	frappe.db.set_value("CRM Lead", lead, FIELD, at, update_modified=False)


def on_lead_before_save(doc, method=None):
	# The stamp is written only via touch(); a whole-doc save carries the
	# client's possibly stale copy, which must not overwrite a newer value.
	if not doc.is_new():
		doc.set(FIELD, frappe.db.get_value("CRM Lead", doc.name, FIELD))


def on_lead_update(doc, method=None):
	if _skip() or not is_human(frappe.session.user):
		return
	old = doc.get_doc_before_save()
	if old and old.status != doc.status:
		touch(doc.name)


def on_comment_insert(doc, method=None):
	if doc.reference_doctype != "CRM Lead" or doc.comment_type != "Comment":
		return
	if _skip() or not is_human(doc.owner) or is_automated_comment(doc.content):
		return
	touch(doc.reference_name, doc.creation)


def on_note_update(doc, method=None):
	if doc.reference_doctype != "CRM Lead" or _skip() or not is_human(frappe.session.user):
		return
	touch(doc.reference_docname)


def is_user_call(call_type: str | None, status: str | None) -> bool:
	return call_type == "Outgoing" or status == "Completed"


def on_call_log_update(doc, method=None):
	# Telephony webhooks save calls as Guest, so the actor is the call itself.
	if _skip() or not is_user_call(doc.type, doc.status):
		return
	touch(doc._resolve_linked_lead(), doc.start_time or doc.creation)


def backfill(lead: str | None = None) -> int:
	"""Fill the stamp from history for existing leads.

	bench --site <site> execute crm.utils.user_activity.backfill
	"""
	system_users = tuple(SYSTEM_USERS)
	lead_filter = "AND l.name = %(lead)s" if lead else ""
	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT l.name, GREATEST(
			COALESCE((SELECT MAX(v.creation) FROM `tabVersion` v
				WHERE v.ref_doctype = 'CRM Lead' AND v.docname = l.name
				AND v.owner NOT IN %(system_users)s
				AND JSON_EXTRACT(v.data, '$.changed[*][0]') LIKE '%%"status"%%'), '1900-01-01'),
			COALESCE((SELECT MAX(c.creation) FROM `tabComment` c
				WHERE c.reference_doctype = 'CRM Lead' AND c.reference_name = l.name
				AND c.comment_type = 'Comment' AND c.owner NOT IN %(system_users)s
				AND (REGEXP_REPLACE(c.content, %(leading_tags)s, '') NOT LIKE '[AUTOMATION]%%'
					OR REGEXP_REPLACE(c.content, %(leading_tags)s, '') LIKE '[AUTOMATION] Quote%%')),
				'1900-01-01'),
			COALESCE((SELECT MAX(n.modified) FROM `tabFCRM Note` n
				WHERE n.reference_doctype = 'CRM Lead' AND n.reference_docname = l.name
				AND n.modified_by NOT IN %(system_users)s), '1900-01-01'),
			COALESCE((SELECT MAX(COALESCE(cl.start_time, cl.creation)) FROM `tabCRM Call Log` cl
				WHERE (cl.type = 'Outgoing' OR cl.status = 'Completed')
				AND ((cl.reference_doctype = 'CRM Lead' AND cl.reference_docname = l.name)
				OR EXISTS (SELECT 1 FROM `tabDynamic Link` dl
					WHERE dl.parenttype = 'CRM Call Log' AND dl.parent = cl.name
					AND dl.link_doctype = 'CRM Lead' AND dl.link_name = l.name))),
				'1900-01-01')
		) AS last_at
		FROM `tabCRM Lead` l
		WHERE l.{FIELD} IS NULL {lead_filter}
		""",
		{"system_users": system_users, "lead": lead, "leading_tags": _LEADING_TAGS.pattern},
	)
	count = 0
	for name, last_at in rows:
		if last_at and str(last_at) > "1900-01-01 00:00:00":
			frappe.db.set_value("CRM Lead", name, FIELD, last_at, update_modified=False)
			count += 1
	return count

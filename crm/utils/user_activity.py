"""Track the last time a human (not automation) acted on a CRM Lead.

Mirrors frontend/src/utils/activityCategory.js, which splits the Activity
timeline into User / System using the same rules.
"""

import re

import frappe
from frappe.core.doctype.version.version import get_diff
from frappe.utils import get_datetime, now_datetime

FIELD = "custom_last_user_activity_on"
SYSTEM_USERS = frozenset({"Administrator", "Guest"})
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


def on_lead_update(doc, method=None):
	if _skip() or not is_human(frappe.session.user):
		return
	old = doc.get_doc_before_save()
	if old and not get_diff(old, doc):
		return
	now = now_datetime()
	doc.set(FIELD, now)
	touch(doc.name, now)


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


def on_call_log_update(doc, method=None):
	# Telephony webhooks save calls as Guest, but every call is a person's action.
	if _skip():
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
				AND v.owner NOT IN %(system_users)s), '1900-01-01'),
			COALESCE((SELECT MAX(c.creation) FROM `tabComment` c
				WHERE c.reference_doctype = 'CRM Lead' AND c.reference_name = l.name
				AND c.comment_type = 'Comment' AND c.owner NOT IN %(system_users)s
				AND (c.content NOT LIKE '[AUTOMATION]%%' OR c.content LIKE '[AUTOMATION] Quote%%')),
				'1900-01-01'),
			COALESCE((SELECT MAX(n.modified) FROM `tabFCRM Note` n
				WHERE n.reference_doctype = 'CRM Lead' AND n.reference_docname = l.name
				AND n.modified_by NOT IN %(system_users)s), '1900-01-01'),
			COALESCE((SELECT MAX(COALESCE(cl.start_time, cl.creation)) FROM `tabCRM Call Log` cl
				WHERE cl.reference_doctype = 'CRM Lead' AND cl.reference_docname = l.name),
				'1900-01-01'),
			IF(l.owner NOT IN %(system_users)s, l.creation, '1900-01-01')
		) AS last_at
		FROM `tabCRM Lead` l
		WHERE l.{FIELD} IS NULL {lead_filter}
		""",
		{"system_users": system_users, "lead": lead},
	)
	count = 0
	for name, last_at in rows:
		if last_at and str(last_at) > "1900-01-01 00:00:00":
			frappe.db.set_value("CRM Lead", name, FIELD, last_at, update_modified=False)
			count += 1
	return count

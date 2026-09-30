"""Access rules for CRM Telephony Agent.

A Telephony Agent record decides which phone Exotel rings for a user (mobile number, Exophone,
browser softphone), so only managers may create or change it. Everyone else sees only their own
record and may change only their own calling preferences.

Managers are ``role_config.TELEPHONY_AGENT_MANAGER_ROLES``; the frontend reads the same set.
"""

import frappe
from frappe import _
from frappe.model import table_fields

from crm.permissions.role_config import TELEPHONY_AGENT_MANAGER_ROLES

AGENT_SELF_EDITABLE_FIELDS = frozenset({"default_medium"})


def is_telephony_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(TELEPHONY_AGENT_MANAGER_ROLES.intersection(frappe.get_roles(user)))


def get_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if is_telephony_manager(user):
		return ""
	return f"`tabCRM Telephony Agent`.`user` = {frappe.db.escape(user)}"


def has_permission(doc, ptype: str | None = None, user: str | None = None, debug: bool = False) -> bool:
	user = user or frappe.session.user
	if is_telephony_manager(user):
		return True
	if ptype in ("create", "delete"):
		return False
	# Judge by the stored owner so a request can't re-point a record at itself to gain access.
	owner = doc.user if doc.is_new() else frappe.db.get_value("CRM Telephony Agent", doc.name, "user")
	return owner == user


def validate_self_edit(doc) -> None:
	"""Stop non-managers from changing anything but their own calling preferences.

	Server-side helpers that save with ignore_permissions (e.g. set_default_calling_medium) are trusted.
	"""
	if doc.flags.ignore_permissions or is_telephony_manager():
		return
	if doc.is_new():
		frappe.throw(_("Only a manager can set up a Telephony Agent."), frappe.PermissionError)

	before = doc.get_doc_before_save()
	changed = [
		df.fieldname
		for df in doc.meta.fields
		if df.fieldname not in AGENT_SELF_EDITABLE_FIELDS
		and _field_value(doc, df) != _field_value(before, df)
	]
	if changed:
		frappe.throw(
			_("Only a manager can change {0}.").format(", ".join(doc.meta.get_label(f) for f in changed)),
			frappe.PermissionError,
		)


def _field_value(doc, df):
	# Child tables count too: set_primary() copies phone_nos' primary row into mobile_no after this check.
	if df.fieldtype in table_fields:
		child_fields = [f.fieldname for f in frappe.get_meta(df.options).fields]
		return [{f: str(row.get(f) or "") for f in child_fields} for row in doc.get(df.fieldname) or []]
	return str(doc.get(df.fieldname) or "")

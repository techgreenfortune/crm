"""Admin-configurable field visibility (INDF-2009).

Field groups are code-owned; which roles/users a group is hidden from is configured
in FCRM Settings → ``restricted_field_rules``. Everyone sees every group by default.

Every enforcement point (doc load, list APIs, activity feed, exports, layouts, the
frontend mandatory check) asks :func:`get_hidden_fields` and nothing else, so new
rule types only change this module.
"""

import re

import frappe
from frappe import _

from crm.permissions.role_config import RESTRICTED_FIELD_EXEMPT_ROLES

FIELD_GROUPS: dict[str, dict] = {
	"Quote Margin": {
		"fields": {
			"CRM Quote Request": ("quote_margin",),
			"CRM Lead": ("custom_final_margin",),
		},
		# Written by CRMQuoteRequest on upload: "[AUTOMATION] Quote uploaded — Value: X, Margin: Y%".
		"comment_patterns": (r",?\s*Margin:\s*[-\d.,]+\s*%",),
	},
}


def _rules() -> list:
	return frappe.get_cached_doc("FCRM Settings").get("restricted_field_rules") or []


def get_hidden_groups(user: str | None = None) -> frozenset[str]:
	user = user or frappe.session.user
	if user == "Administrator":
		return frozenset()
	roles = set(frappe.get_roles(user))
	if roles & RESTRICTED_FIELD_EXEMPT_ROLES:
		return frozenset()

	hidden = set()
	for rule in _rules():
		if rule.field_group not in FIELD_GROUPS:
			continue
		if (rule.applies_to == "User" and rule.user == user) or (
			rule.applies_to == "Role" and rule.role in roles
		):
			hidden.add(rule.field_group)
	return frozenset(hidden)


def get_hidden_fields(doctype: str, user: str | None = None) -> frozenset[str]:
	return frozenset(
		fieldname
		for group in get_hidden_groups(user)
		for fieldname in FIELD_GROUPS[group]["fields"].get(doctype, ())
	)


def strip_doc(doc) -> None:
	for fieldname in get_hidden_fields(doc.doctype):
		doc.__dict__.pop(fieldname, None)


def strip_rows(doctype: str, rows: list) -> list:
	hidden = get_hidden_fields(doctype)
	if hidden:
		for row in rows or []:
			for fieldname in hidden:
				if fieldname in row:
					row[fieldname] = None
	return rows


def _bare_fieldname(field: str) -> str:
	field = re.split(r"\s+as\s+", field.strip(), flags=re.IGNORECASE)[0]
	return field.split(".")[-1].strip("`")


def filter_fields(doctype: str, fields: list) -> list:
	"""Drop hidden fields from a ``get_list``-style ``fields`` list."""
	hidden = get_hidden_fields(doctype)
	if not hidden:
		return fields
	return [f for f in fields if not (isinstance(f, str) and _bare_fieldname(f) in hidden)]


def mask_text(text: str | None) -> str | None:
	if not text:
		return text
	for group in get_hidden_groups():
		for pattern in FIELD_GROUPS[group].get("comment_patterns", ()):
			text = re.sub(pattern, "", text)
	return text


def strip_version_changes(doctype: str, data: dict) -> dict:
	"""Remove hidden fields from a Version ``data`` dict's ``changed`` list."""
	hidden = get_hidden_fields(doctype)
	if hidden and data.get("changed"):
		data["changed"] = [c for c in data["changed"] if c and c[0] not in hidden]
	return data


def restore_hidden_on_save(doc) -> None:
	"""A user never receives hidden values, so whatever they send back is not an edit.

	``as_dict`` re-adds stripped numeric fields as ``0``, so the client echoes ``0`` (not
	``None``) on whole-doc save. Must run before any in-save sync that legitimately writes
	the field (e.g. CRMLead._sync_accepted_quote_on_won).
	"""
	if doc.is_new():
		return
	hidden = get_hidden_fields(doc.doctype)
	if not hidden:
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	for fieldname in hidden:
		doc.set(fieldname, before.get(fieldname))


def validate_rules(rules: list) -> None:
	for rule in rules:
		if rule.field_group not in FIELD_GROUPS:
			frappe.throw(_("Unknown field group: {0}").format(rule.field_group))
		if rule.applies_to == "Role":
			rule.user = None
		else:
			rule.role = None


def _require_system_manager() -> None:
	# Not frappe.only_for: it is a no-op under flags.in_test, so the guard would be untestable.
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def get_my_hidden_fields() -> dict[str, list[str]]:
	hidden: dict[str, set] = {}
	for group in get_hidden_groups():
		for doctype, fieldnames in FIELD_GROUPS[group]["fields"].items():
			hidden.setdefault(doctype, set()).update(fieldnames)
	return {doctype: sorted(fieldnames) for doctype, fieldnames in hidden.items()}


@frappe.whitelist()
def get_restricted_field_settings() -> dict:
	_require_system_manager()
	return {
		"field_groups": sorted(FIELD_GROUPS),
		"exempt_roles": sorted(RESTRICTED_FIELD_EXEMPT_ROLES),
		"rules": [
			{"field_group": r.field_group, "applies_to": r.applies_to, "role": r.role, "user": r.user}
			for r in _rules()
		],
	}


@frappe.whitelist(methods=["POST"])
def save_restricted_field_rules(rules: str | list) -> dict:
	_require_system_manager()
	settings = frappe.get_doc("FCRM Settings")
	settings.set("restricted_field_rules", [])
	for rule in frappe.parse_json(rules) or []:
		settings.append(
			"restricted_field_rules",
			{
				"field_group": rule.get("field_group"),
				"applies_to": rule.get("applies_to") or "Role",
				"role": rule.get("role"),
				"user": rule.get("user"),
			},
		)
	settings.save()
	return get_restricted_field_settings()

"""Admin-configurable field visibility (INDF-2009).

Field groups are code-owned; which roles/users a group is hidden from is configured
in FCRM Settings → ``restricted_field_rules``. Everyone sees every group by default.

Enforcement hooks Frappe's own field-permission chokepoints (see :func:`install`), so
every API that already respects permlevels — ``get_list``/``/api/resource``/reportview/
export, ``frappe.client.*``, desk ``getdoc``, save responses — also respects these rules,
for any doctype listed in :data:`FIELD_GROUPS`. ``ignore_permissions`` / ``frappe.get_all``
server code is unaffected by design.
"""

import re

import frappe
from frappe import _
from frappe.utils.caching import request_cache

from crm.permissions.role_config import RESTRICTED_FIELD_EXEMPT_ROLES

FIELD_GROUPS: dict[str, dict] = {
	"Quote Margin": {
		"fields": {
			"CRM Quote Request": ("quote_margin",),
			"CRM Lead": ("custom_final_margin",),
		},
		# Legacy upload comments: "[AUTOMATION] Quote uploaded — Value: X, Margin: Y%".
		# New comments no longer carry the margin.
		"comment_patterns": (r",?\s*Margin:\s*[-\d.,]+\s*%",),
	},
}

RESTRICTED_DOCTYPES = frozenset(dt for group in FIELD_GROUPS.values() for dt in group["fields"])

_HIDDEN_MARK = "_restricted_hidden"


def _rules() -> list:
	return frappe.get_cached_doc("FCRM Settings").get("restricted_field_rules") or []


@request_cache
def _hidden_groups_for(user: str) -> frozenset[str]:
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


def clear_cache() -> None:
	cache = getattr(frappe.local, "request_cache", None)
	if cache is not None:
		cache.pop(_hidden_groups_for.__wrapped__, None)


def get_hidden_groups(user: str | None = None) -> frozenset[str]:
	return _hidden_groups_for(user or frappe.session.user)


def get_hidden_fields(doctype: str, user: str | None = None) -> frozenset[str]:
	if doctype not in RESTRICTED_DOCTYPES:
		return frozenset()
	return frozenset(
		fieldname
		for group in get_hidden_groups(user)
		for fieldname in FIELD_GROUPS[group]["fields"].get(doctype, ())
	)


def _mark_hidden(doc) -> None:
	"""Drop hidden values from ``doc`` and remember them so ``as_dict`` omits them too
	(``get_valid_dict`` would otherwise re-add a missing numeric field as ``0``)."""
	hidden = get_hidden_fields(doc.doctype)
	if not hidden:
		return
	for fieldname in hidden:
		doc.__dict__.pop(fieldname, None)
	doc.__dict__[_HIDDEN_MARK] = hidden


def strip_doc(doc) -> None:
	_mark_hidden(doc)


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

	Runs inside ``validate_higher_perm_levels`` — before controller hooks, so in-save
	syncs that legitimately write the field (CRMLead._sync_accepted_quote_on_won) and
	``ignore_permissions`` server saves are untouched. New docs are skipped: server code
	(``request_quote``) seeds the value on insert.
	"""
	if doc.flags.ignore_permissions or frappe.flags.in_install or doc.is_new():
		return
	hidden = get_hidden_fields(doc.doctype)
	if not hidden:
		return
	current = frappe.db.get_value(doc.doctype, doc.name, list(hidden), as_dict=True) or {}
	for fieldname in hidden:
		doc.set(fieldname, current.get(fieldname))


_installed = False


def install() -> None:
	"""Patch Frappe's field-permission chokepoints. Idempotent; called on ``import crm``."""
	global _installed
	if _installed:
		return
	_installed = True

	from frappe.model.base_document import BaseDocument
	from frappe.model.document import Document
	from frappe.model.meta import Meta

	orig_permitted = Meta.get_permitted_fieldnames

	def get_permitted_fieldnames(self, *args, **kwargs):
		fieldnames = orig_permitted(self, *args, **kwargs)
		hidden = get_hidden_fields(self.name, kwargs.get("user"))
		if not hidden:
			return fieldnames
		return [f for f in fieldnames if (getattr(f, "fieldname", f)) not in hidden]

	orig_read = Document.apply_fieldlevel_read_permissions

	def apply_fieldlevel_read_permissions(self):
		orig_read(self)
		_mark_hidden(self)
		for table_field in self.meta.get_table_fields():
			for child in self.get(table_field.fieldname) or []:
				_mark_hidden(child)

	orig_as_dict = BaseDocument.as_dict

	def as_dict(self, *args, **kwargs):
		d = orig_as_dict(self, *args, **kwargs)
		marked = self.__dict__.get(_HIDDEN_MARK)
		if marked:
			# Re-check against the current user: a marked doc may be pickled into a job.
			for fieldname in marked & get_hidden_fields(self.doctype):
				d.pop(fieldname, None)
			d.pop(_HIDDEN_MARK, None)
		return d

	orig_validate_perm = Document.validate_higher_perm_levels

	def validate_higher_perm_levels(self):
		orig_validate_perm(self)
		restore_hidden_on_save(self)

	orig_post_save = Document.run_post_save_methods

	def run_post_save_methods(self):
		orig_post_save(self)
		# Hooks above saw real values; the response (client.set_value/save/insert) must not.
		if self.doctype in RESTRICTED_DOCTYPES and get_hidden_fields(self.doctype):
			self.__dict__[_HIDDEN_MARK] = get_hidden_fields(self.doctype)

	Meta.get_permitted_fieldnames = get_permitted_fieldnames
	Document.apply_fieldlevel_read_permissions = apply_fieldlevel_read_permissions
	BaseDocument.as_dict = as_dict
	Document.validate_higher_perm_levels = validate_higher_perm_levels
	Document.run_post_save_methods = run_post_save_methods


def validate_rules(rules: list) -> None:
	for rule in rules:
		if rule.field_group not in FIELD_GROUPS:
			frappe.throw(_("Unknown field group: {0}").format(rule.field_group))
		if rule.applies_to == "Role":
			rule.user = None
			if not rule.role:
				frappe.throw(_("Row {0}: Role is required").format(rule.idx))
		else:
			rule.role = None
			if not rule.user:
				frappe.throw(_("Row {0}: User is required").format(rule.idx))


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

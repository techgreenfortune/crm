import frappe
from frappe import _


ROLE_STAGE_FILTER = {
	"B2F Team": {"C7"},
	"Estimation Team": {"C2-Q"},
}


def has_permission(doc, ptype, user):
	"""Single-doc gate for B2F Team (C7 only) and Estimation Team (C2-Q only).
	List/Kanban filtering is handled by the matching Permission Query Server Script;
	this closes the direct /api/resource/CRM Lead/<name> bypass."""
	if user == "Administrator":
		return True

	user_roles = set(frappe.get_roles(user))
	# Safe because no role profile bundles Sales Manager with B2F Team / Estimation Team
	# (see crm/fixtures/role_profile.json). Revisit if that separation ever changes.
	if "System Manager" in user_roles or "Sales Manager" in user_roles:
		return True

	allowed_stages = set()
	for role, stages in ROLE_STAGE_FILTER.items():
		if role in user_roles:
			allowed_stages |= stages

	if not allowed_stages:
		return True

	if ptype == "create":
		blocked = ", ".join(role for role in ROLE_STAGE_FILTER if role in user_roles)
		frappe.throw(
			_("Users with role '{0}' are not allowed to create leads.").format(blocked),
			exc=frappe.PermissionError,
			title=_("Not Permitted"),
		)

	# Doctype-level checks (no specific doc) pass through — list filtering is the
	# Permission Query script's job. Blocking here would hide the list entirely.
	if not doc or isinstance(doc, str):
		return True

	return getattr(doc, "status", None) in allowed_stages

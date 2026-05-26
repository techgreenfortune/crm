import frappe
from frappe import _

from crm.permissions.role_config import ROLE_PRIORITY, ROLE_RANK

# Any user with a CRM custom role (the 13-role matrix in role_config.ROLE_RANK)
# may access CRM resources. Replaces the legacy ["System Manager", "Sales
# Manager", "Sales User"] list after the Sales Manager retirement on
# 2026-05-25 — tier-1 managers no longer carry Sales Manager (or Sales User),
# so the gate has to recognise the custom roles directly.
CRM_ALLOWED_ROLES = frozenset(ROLE_RANK)


def get_session_role_flags():
	roles = set(frappe.get_roles())

	if not roles & CRM_ALLOWED_ROLES:
		frappe.throw(_("You are not permitted to access CRM resources."), frappe.PermissionError)

	return {
		"is_system_manager": "System Manager" in roles,
	}


@frappe.whitelist()
def get_users():
	session_roles = get_session_role_flags()

	users = frappe.qb.get_query(
		"User",
		fields=[
			"name",
			"email",
			"enabled",
			"user_image",
			"first_name",
			"last_name",
			"full_name",
			"user_type",
			"language",
		],
		order_by="full_name asc",
		distinct=True,
		filters={"enabled": 1},
	).run(as_dict=1)

	crm_users = []
	system_language = frappe.db.get_single_value("System Settings", "language")

	for user in users:
		if frappe.session.user == user.name:
			user.session_user = True

		user.roles = frappe.get_roles(user.name)

		user.role = ""
		for role in ROLE_PRIORITY:
			if role in user.roles:
				user.role = role
				break
		else:
			if "Guest" in user.roles:
				user.role = "Guest"

		if frappe.session.user == user.name:
			user.session_user = True

		user.is_telephony_agent = frappe.db.exists("CRM Telephony Agent", {"user": user.name})
		user.language = user.language or system_language

		if user.role and user.role != "Guest":
			crm_users.append(user)

	if not session_roles["is_system_manager"]:
		users = crm_users

	return users, crm_users


@frappe.whitelist()
def get_assignable_users():
	"""Return the list of CRM users the session user may assign CRM Leads to.

	UX-only helper for the assignee pickers in AssignToBody / AssignmentModal —
	the backend enforcement lives in ``crm_lead_permissions.guard_lead_assignment``
	(ToDo) and ``CRMLead._check_write_permission`` (lead_owner). Filtering the
	picker keeps users from choosing an option that would then throw.

	Returns the *full* CRM users list for Administrator, tier-1 roles, and roles
	without a tree to restrict to (everyone except ASM/RSM). ASM/RSM with a
	hierarchy node get only their downstream subtree + direct upline; orphan
	ASM/RSM bypass the filter to match the backend skip.
	"""
	get_session_role_flags()
	user = frappe.session.user
	_, crm_users = get_users()

	if user == "Administrator":
		return crm_users

	roles = set(frappe.get_roles(user))
	from crm.overrides.crm_lead_permissions import allowed_assignees
	from crm.permissions.role_config import DOWNSTREAM_SCOPE_ROLES, TIER1_FULL_RW

	if roles & TIER1_FULL_RW:
		return crm_users
	if not (roles & DOWNSTREAM_SCOPE_ROLES):
		return crm_users

	allowed = allowed_assignees(user)
	if allowed is None:
		return crm_users
	return [u for u in crm_users if u.name in allowed]


@frappe.whitelist()
def get_organizations():
	get_session_role_flags()

	organizations = frappe.qb.get_query(
		"CRM Organization",
		fields=["*"],
		order_by="name asc",
		distinct=True,
	).run(as_dict=1)

	return organizations


@frappe.whitelist()
def get_accounts():
	get_session_role_flags()

	accounts = frappe.qb.get_query(
		"CRM Account",
		fields=["*"],
		order_by="name asc",
		distinct=True,
	).run(as_dict=1)

	return accounts

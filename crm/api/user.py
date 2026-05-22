import frappe
from frappe import _
from frappe.auth import LoginAttemptTracker
from frappe.rate_limiter import rate_limit
from frappe.utils.password import check_password, update_password

# Custom CRM role profile names (must exist in `crm/fixtures/role_profile.json`).
# System Manager is handled out-of-band — it isn't a Role Profile, it's the
# Frappe built-in admin role granted directly.
CRM_ROLE_PROFILES = (
	"Sales Head",
	"RSM",
	"ASM",
	"Sales Executive",
	"Project Sales Executive",
	"Sales Coordinator",
	"Marketing",
	"Calling Team",
	"Jr. Sales Executive",
	"B2F Team",
	"Estimation Team",
	"Management",
)

# Roles that can invite/promote/demote CRM users.
_ADMIN_ROLES = ("System Manager", "Sales Head")


@frappe.whitelist()
@rate_limit(limit=5, seconds=300)  # 5 attempts per 5 minutes per user/IP
def change_password(old_password: str, new_password: str):
	"""
	Change password for the current logged-in user.
	Uses Frappe's LoginAttemptTracker for attempt counting/lockout, and rate_limit for API abuse protection.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("You must be logged in to change your password"), frappe.AuthenticationError)

	tracker = LoginAttemptTracker(user)
	if not tracker.is_user_allowed():
		frappe.throw(_("Too many failed attempts. Please try again after some time."))

	if old_password == new_password:
		frappe.throw(
			_("New password cannot be the same as current password. Please choose a different password.")
		)

	try:
		check_password(user, old_password)
	except frappe.AuthenticationError:
		tracker.add_failure_attempt()
		frappe.throw(_("Incorrect current password. Please try again."))
	else:
		tracker.add_success_attempt()

	# Validate new password strength (server-side enforcement)
	from frappe.core.doctype.user.user import test_password_strength

	result = test_password_strength(new_password)
	feedback = result.get("feedback", {})
	if not feedback.get("password_policy_validation_passed", False):
		suggestions = feedback.get("suggestions", [])
		frappe.throw(_("Password is too weak. {0}").format(" ".join(suggestions) if suggestions else ""))

	update_password(user=user, pwd=new_password, logout_all_sessions=False)
	return _("Password Updated Successfully")


@frappe.whitelist()
def add_existing_users(users: str | list, role: str = "Sales Executive"):
	"""Add existing Frappe users to the CRM by assigning them a CRM role profile.

	``role`` is a Role Profile name from :data:`CRM_ROLE_PROFILES` or
	"System Manager" (the Frappe built-in role, not a profile).
	"""
	frappe.only_for(_ADMIN_ROLES, True)
	is_system_manager = "System Manager" in frappe.get_roles()

	if role == "System Manager" and not is_system_manager:
		frappe.throw(_("Only System Managers can assign the System Manager role"), frappe.PermissionError)

	if role not in CRM_ROLE_PROFILES and role != "System Manager":
		frappe.throw(_("Invalid role profile: {0}").format(role))

	users = frappe.parse_json(users)
	for user in users:
		update_user_role(user, role)


@frappe.whitelist()
def update_user_role(user: str, new_role: str):
	"""Update a user's CRM role.

	``new_role`` is a Role Profile name from :data:`CRM_ROLE_PROFILES`, or
	"System Manager" to grant Frappe's built-in admin role.
	"""
	frappe.only_for(_ADMIN_ROLES, True)
	is_system_manager = "System Manager" in frappe.get_roles()

	if new_role != "System Manager" and new_role not in CRM_ROLE_PROFILES:
		frappe.throw(_("Cannot assign this role"))

	user_doc = frappe.get_doc("User", user)
	target_roles = {d.role for d in user_doc.roles}
	target_is_system_manager = "System Manager" in target_roles

	if new_role == "System Manager" and not is_system_manager:
		frappe.throw(_("Only System Managers can assign the System Manager role"), frappe.PermissionError)

	if target_is_system_manager and not is_system_manager:
		frappe.throw(_("Only System Managers can modify other System Managers"), frappe.PermissionError)

	# Hierarchy data integrity: profiles that don't bundle Sales Manager
	# (i.e. non-managerial) cannot be assigned to a user who is a root or has
	# direct reports in the CRM Sales Hierarchy tree — that would orphan their
	# reports. Admin must remove them from the hierarchy first.
	_NON_MANAGERIAL_PROFILES = {
		"Sales Executive",
		"Project Sales Executive",
		"Marketing",
		"Calling Team",
		"Jr. Sales Executive",
		"B2F Team",
		"Estimation Team",
	}
	if new_role in _NON_MANAGERIAL_PROFILES:
		node = frappe.db.get_value(
			"CRM Sales Hierarchy", {"user": user}, ["name", "reports_to"], as_dict=True
		)
		if node:
			has_reports = frappe.db.exists("CRM Sales Hierarchy", {"reports_to": node.name})
			if has_reports or not node.reports_to:
				frappe.throw(
					_("Remove this user from the sales hierarchy before changing their role to a non-managerial profile.")
				)

	if new_role == "System Manager":
		# System Manager is a direct role assignment (Frappe built-in).
		user_doc.append_roles("System Manager")
		user_doc.set("block_modules", [])
	else:
		# CRM role profiles handle their own role bundling via fixtures
		# (CRM User + the custom role). Setting role_profile_name propagates
		# the bundled roles on save.
		user_doc.role_profile_name = new_role
		update_module_in_user(user_doc, "FCRM")

	user_doc.save(ignore_permissions=True)


@frappe.whitelist()
def remove_crm_roles_from_user(user: str):
	"""Remove a user from CRM by clearing their role profile and CRM role assignments."""
	frappe.only_for(_ADMIN_ROLES, True)

	if user == frappe.session.user:
		frappe.throw(_("You cannot remove yourself."), frappe.PermissionError)

	user_doc = frappe.get_doc("User", user)
	roles = {d.role for d in user_doc.roles}

	current_user_is_system_manager = "System Manager" in frappe.get_roles()

	if "System Manager" in roles and not current_user_is_system_manager:
		frappe.throw(_("Only System Managers can modify other System Managers"), frappe.PermissionError)

	if user_doc.get("role_profiles") or user_doc.get("role_profile_name"):
		user_doc.role_profile_name = None
		user_doc.set("role_profiles", [])

	# Strip any CRM-specific roles + the base "CRM User" / "Sales User" that
	# might still be attached from legacy seeding.
	cleanup = set(CRM_ROLE_PROFILES) | {"CRM User", "Sales User", "Sales Manager"}
	remove_roles(user_doc, *(r for r in cleanup if r in roles))

	user_doc.save(ignore_permissions=True)

	# Also remove the user's CRM Sales Hierarchy node if it exists — orphans
	# any direct reports (they become top-level). Admin can re-parent them via
	# the hierarchy UI.
	node_name = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, "name")
	if node_name:
		frappe.delete_doc("CRM Sales Hierarchy", node_name, ignore_permissions=True)

	frappe.msgprint(_("User {0} has been removed from CRM roles.").format(user))


def remove_roles(self, *roles):
	existing_roles = {d.role: d for d in self.get("roles")}
	for role in roles:
		if role in existing_roles:
			self.get("roles").remove(existing_roles[role])


def update_module_in_user(user, module):
	block_modules = frappe.get_all(
		"Module Def",
		fields=["name as module"],
		filters={"name": ["!=", module]},
	)

	if block_modules:
		user.set("block_modules", block_modules)

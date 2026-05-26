"""CRM Lead permissions for the 13-role IndiFrame access matrix.

Implements:
- ``has_permission`` hook (single-doc gate).
- ``get_permission_query_conditions`` (list-level filter; wired in hooks.py).
- ``downstream_users`` helper for ASM/RSM scoping. Reads from the
  ``CRM Sales Hierarchy`` NestedSet doctype (adopted from upstream PR #2120
  while keeping our 13-role matrix as the policy layer).

Access matrix:

- Admin / System Manager / Sales Head / Sales Coordinator: full RW, incl. unassigned
- Calling Team: all non-C7 leads, incl. unassigned; can create leads
- Management: read-only across assigned leads
- Marketing: read all assigned + write (field-level lock in CRMLead.validate())
- B2F Team: C7 assigned only + field-locked writes
- Estimation Team: assigned leads with an active (Pending / Quote Received / Revision Requested) Quote Request
- Jr. Sales Executive: all non-C7 assigned leads (no unassigned pool access)
- Sales Executive: own leads, ``custom_lead_type == 'Retail'`` only
- Project Sales Executive: own leads, ``custom_lead_type == 'Projects'`` only
- ASM / RSM: own + downstream-chain leads, any lead type

The Retail/Projects split applies only to the leaf-level Sales Executive /
Project Sales Executive roles. Managers (ASM / RSM) see every lead owned by
anyone in their downstream chain regardless of lead type — so a lead assigned
to a Sales Executive (Retail) and an Engineer-PSE under the same ASM are both
visible to that ASM and to the RSM above them.

Unassigned leads (``lead_owner`` is NULL/empty) are visible only to
``UNASSIGNED_VISIBLE_ROLES`` — Tier-1 full-RW plus Calling Team. Every other
role sees only leads with an owner. This makes the Calling Team the explicit
inbox for new leads: they create + first-touch, then assign downstream.

Multi-role users get the union of allow-clauses. The list-level query and the
single-doc gate must stay consistent — both call the same role-tier helpers.

Onboarding: invite the user via Settings → Invite Users, then add them to the
Sales Hierarchy tree at Settings → Sales Hierarchy if they participate in
ASM/RSM downstream scoping. Cache invalidates on every hierarchy save.
"""

import frappe
from frappe import _

from crm.permissions.role_config import (
	DOWNSTREAM_SCOPE_ROLES,
	FIELD_GATED_RW,
	OWNER_SCOPE_ROLES,
	QUOTE_SCOPE_ROLES,
	STAGE_LOCKED,
	TIER1_FULL_RW,
	TIER1_READ_ONLY,
)

# Roles that cannot create a CRM Lead. Calling Team is intentionally NOT in
# this set — they own the unassigned inbox and create new leads. JSE is still
# blocked from creating (despite sharing the non-C7 visibility bucket with
# Calling Team) because they're a leaf-level sales role, not the lead-intake
# team. Management is included (read-only role) because there's no longer an
# early-return that would block it before the create gate.
_NO_CREATE_ROLES = (
	set(STAGE_LOCKED)
	| set(QUOTE_SCOPE_ROLES)
	| set(OWNER_SCOPE_ROLES)
	| set(TIER1_READ_ONLY)
	| {"Jr. Sales Executive", "Marketing"}
)

# SQL fragment for "the lead has an owner" — used to gate non-tier-1,
# non-Calling-Team roles out of the unassigned pool.
_ASSIGNED_ONLY_SQL = "(`tabCRM Lead`.lead_owner IS NOT NULL AND `tabCRM Lead`.lead_owner != '')"

# Quote Request statuses that mean Estimation Team still has work to do on the lead.
_ACTIVE_QR_STATUSES = ("Pending", "Quote Received", "Revision Requested")


def has_permission(doc, ptype, user):
	"""Single-doc gate for the CRM Lead 13-role access matrix.

	The matching list-level filter is :func:`get_permission_query_conditions`
	(wired via ``permission_query_conditions`` in hooks.py). Both must apply
	the same rules so that ``/api/resource/CRM Lead/<name>`` can't reach a
	doc that the list view filtered out.

	Note: Frappe's share grant is OR'd with this hook — if the lead is shared
	with the user (auto-share on assignment, manual share), they see it even
	if this hook returns False. The list query is the hard exclusion.
	"""
	if user == "Administrator":
		return True

	roles = set(frappe.get_roles(user))
	if "System Manager" in roles:
		return True

	# Tier-1 full-RW (Sales Head / Sales Coordinator) — see every lead.
	if roles & TIER1_FULL_RW:
		return True

	# Create gate: pool / stage-locked / owner-scoped / Marketing / JSE
	# roles cannot create leads. Calling Team CAN create (handled by its
	# absence from _NO_CREATE_ROLES). Tier-1 full-RW already returned above.
	if ptype == "create":
		if roles & _NO_CREATE_ROLES:
			frappe.throw(
				_("Users with your role(s) are not allowed to create leads."),
				exc=frappe.PermissionError,
				title=_("Not Permitted"),
			)
		return True

	# Doctype-level check (no specific doc) — defer row filtering to the
	# permission query so the list page renders.
	if not doc or isinstance(doc, str):
		return True

	status = getattr(doc, "status", None)
	owner = getattr(doc, "lead_owner", None)
	lead_type = getattr(doc, "custom_lead_type", None)
	is_unassigned = not owner

	# Calling Team — read+write non-C7 leads, INCLUDING unassigned. Owns the
	# new-lead inbox.
	if "Calling Team" in roles and status != "C7":
		return True

	# Management is read-only on assigned leads — unless the user ALSO carries
	# a writable role (Marketing / B2F / an owner-scoped role / a stage pool /
	# JSE). In that case the writable role wins via the union below. Unassigned
	# leads are gated out regardless.
	if roles & TIER1_READ_ONLY and not (
		roles & (FIELD_GATED_RW | set(OWNER_SCOPE_ROLES) | set(STAGE_LOCKED) | {"Jr. Sales Executive"})
	):
		return ptype == "read" and not is_unassigned

	# Marketing: read-all assigned + writes pass here (field-level locks in
	# CRMLead._check_write_permission).
	if "Marketing" in roles and not is_unassigned:
		return True

	# Quote-scoped roles (Estimation Team) — assigned leads with an active QR.
	if roles & QUOTE_SCOPE_ROLES and not is_unassigned:
		active_qr = frappe.db.exists(
			"CRM Quote Request",
			{"lead": doc.name, "status": ["in", list(_ACTIVE_QR_STATUSES)]},
		)
		if active_qr:
			return True

	# Stage-locked pool roles — assigned leads only.
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if status in pool_stages and not is_unassigned:
		return True

	# Jr. Sales Executive — non-C7 assigned leads. (Calling Team is handled
	# above with broader access incl. unassigned.)
	if "Jr. Sales Executive" in roles and status != "C7" and not is_unassigned:
		return True

	# Owner-scoped roles. SE/PSE are also lead-type-scoped (Retail / Projects);
	# ASM/RSM see any lead type within their downstream chain. Unassigned leads
	# are naturally excluded because owner != user / not in downstream set.
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["lead_type"] and lead_type != rule["lead_type"]:
			continue
		if rule["scope"] == "self" and owner == user:
			return True
		if rule["scope"] == "downstream" and owner in downstream_users(user):
			return True

	return False


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List-level filter for CRM Lead.

	Wired via ``permission_query_conditions["CRM Lead"]`` in hooks.py. Returns
	a SQL WHERE clause (or empty string for "no filter"). Mirrors the rules
	in :func:`has_permission` exactly — the two gates must agree.

	Replaces the legacy "CRM Lead — Permission Query" Server Script, which
	failed under safe_exec because ``frappe.get_attr`` is not exposed in the
	RestrictedPython sandbox (blocking every ASM/RSM list view).
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))

	# Tier-1 full-RW — see every lead including unassigned.
	if roles & TIER1_FULL_RW:
		return ""

	# Calling Team — non-C7 leads, INCLUDING unassigned (the new-lead inbox).
	if "Calling Team" in roles:
		return "(`tabCRM Lead`.status != 'C7' OR `tabCRM Lead`.status IS NULL)"

	# Every other role below sees only ASSIGNED leads (lead_owner present).
	# Unassigned-pool visibility is restricted to UNASSIGNED_VISIBLE_ROLES,
	# which is Tier-1 full-RW + Calling Team (both handled above).

	# Read-all roles (Management read-only, Marketing) — assigned only.
	if roles & (TIER1_READ_ONLY | {"Marketing"}):
		return _ASSIGNED_ONLY_SQL

	# Stage-locked pools (B2F = C7) — assigned only.
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if pool_stages:
		stages_sql = ",".join(f"'{s}'" for s in sorted(pool_stages))
		return f"(`tabCRM Lead`.status IN ({stages_sql}) AND {_ASSIGNED_ONLY_SQL})"

	# Quote-scoped roles (Estimation Team) — assigned leads with an active QR.
	if roles & QUOTE_SCOPE_ROLES:
		_qr_statuses_sql = ",".join(f"'{s}'" for s in _ACTIVE_QR_STATUSES)
		active_qr_sql = (
			"EXISTS (SELECT 1 FROM `tabCRM Quote Request` "
			"WHERE `tabCRM Quote Request`.lead = `tabCRM Lead`.name "
			f"AND `tabCRM Quote Request`.status IN ({_qr_statuses_sql}))"
		)
		return f"({active_qr_sql} AND {_ASSIGNED_ONLY_SQL})"

	# Jr. Sales Executive — non-C7 assigned only.
	if "Jr. Sales Executive" in roles:
		return f"((`tabCRM Lead`.status != 'C7' OR `tabCRM Lead`.status IS NULL) AND {_ASSIGNED_ONLY_SQL})"

	# Owner-scoped roles. Build one clause per role the user holds; OR them.
	esc = frappe.db.escape
	clauses: list[str] = []
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["scope"] == "self":
			owner_clause = f"`tabCRM Lead`.lead_owner = {esc(user)}"
		else:  # downstream
			downstream = downstream_users(user)
			in_list = ",".join(esc(u) for u in sorted(downstream)) or esc(user)
			owner_clause = f"`tabCRM Lead`.lead_owner IN ({in_list})"
		if rule["lead_type"]:
			clauses.append(f"(`tabCRM Lead`.custom_lead_type = '{rule['lead_type']}' AND {owner_clause})")
		else:
			clauses.append(f"({owner_clause})")

	if not clauses:
		# No rule grants any access — exclude everything.
		return "1=0"
	return "(" + " OR ".join(clauses) + ")"


def downstream_users(user: str) -> set[str]:
	"""Return ``{user}`` plus all users at or below ``user`` in CRM Sales Hierarchy.

	Uses NestedSet ``lft / rgt`` for an O(1) range scan on the hierarchy table.
	Cached at a version key; invalidated on every CRM Sales Hierarchy save by
	:func:`bust_downstream_users_cache`. One DB read per cache miss.

	If ``user`` is not in the hierarchy, returns ``{user}`` — single-node
	"hierarchy" so SE/PSE/etc. callers still see their own leads correctly.
	"""
	cache = frappe.cache()
	version = cache.get_value("crm:sales_hierarchy:version") or 0
	cache_key = f"crm:sales_hierarchy:v{version}:downstream:{user}"
	cached = cache.get_value(cache_key)
	if cached is not None:
		return set(cached)

	node = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"], as_dict=True)
	if not node:
		result = {user}
	else:
		rows = frappe.db.sql(
			"SELECT `user` FROM `tabCRM Sales Hierarchy` WHERE lft >= %s AND rgt <= %s",
			(node["lft"], node["rgt"]),
		)
		result = {r[0] for r in rows if r[0]}
		# Defensive: the node itself should always be in the subtree, but a
		# malformed/half-saved row could be missing — guarantee inclusion.
		result.add(user)

	cache.set_value(cache_key, list(result), expires_in_sec=600)
	return result


def bust_downstream_users_cache(doc=None, method=None):
	"""Hooked to ``CRM Sales Hierarchy.on_update`` / ``on_trash`` — bumps a
	shared version counter, invalidating every cached subtree set at once."""
	cache = frappe.cache()
	current = cache.get_value("crm:sales_hierarchy:version") or 0
	cache.set_value("crm:sales_hierarchy:version", int(current) + 1)


# ----------------------------------------------------------------------------
# Tree-scoped lead assignment guard (ASM / RSM)
# ----------------------------------------------------------------------------
#
# Restricts who an ASM/RSM placed in CRM Sales Hierarchy may hand a lead to:
# their downstream subtree plus their direct manager (1 step up). Orphan or
# freshly-invited users (no hierarchy row) bypass the check so onboarding
# isn't blocked. Tier-1 (System Manager / Sales Head / Sales Coordinator)
# bypass too — they keep cross-team transfer rights.
#
# Enforced in two places that must stay in sync:
#   - CRMLead._check_write_permission (lead_owner field change)
#   - guard_lead_assignment (ToDo before_insert; covers _assign + bulk paths)
# ----------------------------------------------------------------------------

# Roles subject to the tree-scoped assignment rule come from role_config's
# OWNER_SCOPE_ROLES (`scope == "downstream"`). Tier-1 (TIER1_FULL_RW) bypass
# the guard — they keep cross-team transfer rights. SE/PSE are self-scope
# leaves; JSE / Calling Team are pool-based — none of them have a meaningful
# "tree" to restrict to, so they're excluded by virtue of not being in
# DOWNSTREAM_SCOPE_ROLES.


def allowed_assignees(user: str) -> set[str] | None:
	"""Return users ``user`` may assign CRM Leads to: downstream subtree
	plus direct upline (1 step). Returns ``None`` if ``user`` has no node
	in ``CRM Sales Hierarchy`` — caller skips the check (orphan / newly
	onboarded)."""
	node = frappe.db.get_value(
		"CRM Sales Hierarchy",
		{"user": user},
		["name", "reports_to"],
		as_dict=True,
	)
	if not node:
		return None
	allowed = set(downstream_users(user))
	if node.reports_to:
		parent_user = frappe.db.get_value("CRM Sales Hierarchy", node.reports_to, "user")
		if parent_user:
			allowed.add(parent_user)
	return allowed


def guard_lead_assignment(doc, method=None):
	"""ToDo ``before_insert`` hook — blocks an ASM/RSM from creating an
	``_assign`` ToDo against a CRM Lead for a user outside their tree.
	Covers both the single (``assign_to.add``) and bulk
	(``assign_to.add_multiple``) flows since both create ToDo rows under
	the hood. Mirrors the ``lead_owner``-change guard in CRMLead."""
	if getattr(doc, "reference_type", None) != "CRM Lead":
		return
	user = frappe.session.user
	if user == "Administrator" or frappe.flags.get("ignore_permissions"):
		return
	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return
	if not (roles & DOWNSTREAM_SCOPE_ROLES):
		return
	allowed = allowed_assignees(user)
	if allowed is None:
		# Orphan / newly-onboarded — skip the check.
		return
	allocated_to = getattr(doc, "allocated_to", None)
	if allocated_to and allocated_to not in allowed:
		frappe.throw(
			_(
				"You can only assign leads to users in your team (your reports "
				"or your manager). {0} is outside your tree — escalate to Sales "
				"Head for cross-team transfers."
			).format(allocated_to),
			frappe.PermissionError,
			title=_("Out-of-tree Assignment Blocked"),
		)

"""CRM Lead permissions for the 13-role IndiFrame access matrix.

Implements:
- ``has_permission`` hook (single-doc gate).
- ``get_permission_query_conditions`` (list-level filter; wired in hooks.py).
- ``_downstream_users`` helper for ASM/RSM scoping. Reads from the
  ``CRM Sales Hierarchy`` NestedSet doctype (adopted from upstream PR #2120
  while keeping our 13-role matrix as the policy layer).

Access matrix:

- Admin / System Manager / Sales Head / Sales Coordinator: full RW
- Management: read-only across all leads
- Marketing: read all + write (field-level lock in CRMLead.validate())
- B2F Team: C7 only + field-locked writes
- Estimation Team: C2 only
- Calling Team / Jr. Sales Executive: all non-C7 leads
- Sales Executive: own leads, ``custom_lead_type == 'Retail'`` only
- Project Sales Executive: own leads, ``custom_lead_type == 'Projects'`` only
- ASM / RSM: own + downstream-chain leads, any lead type

The Retail/Projects split applies only to the leaf-level Sales Executive /
Project Sales Executive roles. Managers (ASM / RSM) see every lead owned by
anyone in their downstream chain regardless of lead type — so a lead assigned
to a Sales Executive (Retail) and an Engineer-PSE under the same ASM are both
visible to that ASM and to the RSM above them.

Multi-role users get the union of allow-clauses. The list-level query and the
single-doc gate must stay consistent — both call the same role-tier helpers.

Onboarding: invite the user via Settings → Invite Users, then add them to the
Sales Hierarchy tree at Settings → Sales Hierarchy if they participate in
ASM/RSM downstream scoping. Cache invalidates on every hierarchy save.
"""

import frappe
from frappe import _

from crm.permissions.role_config import (
	FIELD_GATED_RW,
	NO_C7_ROLES,
	OWNER_SCOPE_ROLES,
	STAGE_LOCKED,
	TIER1_FULL_RW,
	TIER1_READ_ONLY,
)

# Roles that cannot create a CRM Lead (pool / stage-locked / owner-scoped).
_NO_CREATE_ROLES = set(STAGE_LOCKED) | set(OWNER_SCOPE_ROLES) | NO_C7_ROLES | {"Marketing"}


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

	# Management is read-only — unless the user ALSO carries a writable role
	# (Sales Head / Marketing / B2F / an owner-scoped role / a stage pool /
	# Calling Team / JSE). In that case the writable role wins via the union
	# below.
	if roles & TIER1_READ_ONLY and not (
		roles & (TIER1_FULL_RW | FIELD_GATED_RW | set(OWNER_SCOPE_ROLES) | set(STAGE_LOCKED) | NO_C7_ROLES)
	):
		return ptype == "read"

	if roles & TIER1_FULL_RW:
		return True

	# Create gate: pool / stage-locked / owner-scoped / Marketing roles
	# cannot create leads. Only Tier1 full-RW roles (already returned above)
	# or users with none of the denying roles get through.
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

	# Marketing: read-all + writes pass here (field-level locks in
	# CRMLead._check_write_permission).
	if "Marketing" in roles:
		return True

	# Stage-locked pool roles.
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if status in pool_stages:
		return True

	# Calling Team / Jr. Sales Executive — read+write everything except C7.
	if roles & NO_C7_ROLES and status != "C7":
		return True

	# Owner-scoped roles. SE/PSE are also lead-type-scoped (Retail / Projects);
	# ASM/RSM see any lead type within their downstream chain.
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["lead_type"] and lead_type != rule["lead_type"]:
			continue
		if rule["scope"] == "self" and owner == user:
			return True
		if rule["scope"] == "downstream" and owner in _downstream_users(user):
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

	# Tier-1 full-RW + Marketing + Management see every lead.
	if roles & (TIER1_FULL_RW | TIER1_READ_ONLY | {"Marketing"}):
		return ""

	# Stage-locked pools (B2F = C7, Estimation = C2) — Stage union.
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if pool_stages:
		stages_sql = ",".join(f"'{s}'" for s in sorted(pool_stages))
		return f"(`tabCRM Lead`.status IN ({stages_sql}))"

	# Calling Team / Jr. Sales Executive — everything except C7.
	if roles & NO_C7_ROLES:
		return "(`tabCRM Lead`.status != 'C7' OR `tabCRM Lead`.status IS NULL)"

	# Owner-scoped roles. Build one clause per role the user holds; OR them.
	esc = frappe.db.escape
	clauses: list[str] = []
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["scope"] == "self":
			owner_clause = f"`tabCRM Lead`.lead_owner = {esc(user)}"
		else:  # downstream
			downstream = _downstream_users(user)
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


def _downstream_users(user: str) -> set[str]:
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

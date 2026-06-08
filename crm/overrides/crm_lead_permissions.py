"""CRM Lead permissions — pure-hierarchy visibility + per-role write locks.

Implements:
- ``has_permission`` hook (single-doc gate).
- ``get_permission_query_conditions`` (list-level filter; wired in hooks.py).
- ``downstream_users`` helper — reads the ``CRM Sales Hierarchy`` NestedSet
  (adopted from upstream PR #2120) to expand a user into their subtree.
- ``allowed_assignees`` — the ASM/RSM reassign guard used by
  ``CRMLead._check_write_permission``.

Visibility (read):

- Admin / System Manager / Sales Head / Sales Coordinator (``TIER1_FULL_RW``):
  every lead, including ownerless.
- Calling Team: all non-C7 leads incl. ownerless — the new-lead inbox.
- Everyone else: a lead is visible iff its ``lead_owner`` is the user OR a
  member of the user's CRM Sales Hierarchy subtree (``downstream_users``) OR
  the user's direct manager (1 level up — ``upstream_user``, any lead_type).
  A lead owned by someone outside the viewer's tree — or with no owner — is
  invisible. So an orphan-owned lead is seen only by its owner + admin,
  upstream managers see every lead owned by anyone below them, and an
  owner-scoped user additionally sees the leads owned by their direct manager.

Write locks (layered on top — these survive the read model):

- Management (without a writable role) is read-only.
- Marketing / B2F field whitelists + the ASM/RSM cross-team reassign guard
  are enforced in ``CRMLead._check_write_permission``.
- STAGE_LOCKED / QUOTE_SCOPE roles cannot create leads.

The list-level query and the single-doc gate must stay consistent — both use
``downstream_users`` and encode the same Calling-Team / owner-scope rules.

Onboarding: invite the user via Settings → Invite Users, then add them to the
Sales Hierarchy tree at Settings → Sales Hierarchy so their leads roll up to
their managers. Cache invalidates on every hierarchy save.
"""

import frappe
from frappe import _

from crm.permissions.role_config import (
	OWNER_SCOPE_ROLES,
	QUOTE_SCOPE_ROLES,
	STAGE_LOCKED,
	TIER1_FULL_RW,
)

# SQL fragment for "the lead has an owner" — the pool/broad-read roles
# (Marketing / B2F / Estimation) see only ASSIGNED leads; ownerless leads stay
# tier-1 + Calling-Team only.
_ASSIGNED_ONLY_SQL = "(`tabCRM Lead`.lead_owner IS NOT NULL AND `tabCRM Lead`.lead_owner != '')"

# Quote Request statuses that keep an Estimation-pool lead visible — i.e. work
# is still open. Once a QR reaches _TERMINAL_QR_STATUS the lead drops off.
_ACTIVE_QR_STATUSES = ("Pending", "Quote Received", "Revision Requested")
_TERMINAL_QR_STATUS = "Accepted"


def _estimation_qr_gate(lead_name: str) -> bool:
	"""True when a lead has at least one active QR and no accepted QR.

	Single round-trip replacing the previous two frappe.db.exists calls.
	The SQL equivalent lives in get_permission_query_conditions (Estimation block).
	Both must stay in lockstep — update _ACTIVE_QR_STATUSES / _TERMINAL_QR_STATUS
	to change the gate for both paths at once.
	"""
	statuses = set(frappe.db.get_all("CRM Quote Request", filters={"lead": lead_name}, pluck="status"))
	return bool(statuses & set(_ACTIVE_QR_STATUSES)) and _TERMINAL_QR_STATUS not in statuses


def has_permission(doc, ptype, user):
	"""Single-doc gate for CRM Lead. Visibility is the UNION of every gate the
	user's roles unlock — the same set of clauses :func:`get_permission_query_conditions`
	OR's together, so the two stay in lockstep (round-trip tested).

	Gates:
	  - Administrator / ``TIER1_FULL_RW`` (System Manager, Sales Head, Sales
	    Coordinator, Management) → every lead, full read-write.
	  - Calling Team → all non-C7 leads incl. ownerless (new-lead inbox).
	  - Marketing → all assigned leads (writes field-locked in
	    ``_check_write_permission``).
	  - B2F Team (pool) → all assigned C7 leads.
	  - Estimation Team (pool) → all assigned leads with an active Quote Request
	    (Pending / Quote Received / Revision Requested) and no Accepted QR
	    (Accepted is terminal).
	  - Owner-scoped (SE = own Retail, PSE = own Projects, Spotter/JSE = own any
	    type, ASM/RSM = own + downstream subtree any type).

	Ownerless leads are visible only to tier-1 + Calling Team. Reassignment
	targets are validated separately by the write guard. Frappe's share grant is
	OR'd with this hook by the framework.
	"""
	if user == "Administrator":
		return True

	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return True

	if ptype == "create":
		if roles & (set(STAGE_LOCKED) | QUOTE_SCOPE_ROLES):
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

	# Base visibility on the PERSISTED owner/status/type for an existing doc, not
	# the in-memory values. A pending ``lead_owner`` change (reassignment) is
	# validated separately by the write guard in CRMLead._check_write_permission;
	# if we used the in-memory new owner here, an owner handing a lead UP to their
	# manager would lose write access mid-save (the new owner is outside their
	# subtree) and Frappe would block the very save that performs the handoff.
	name = getattr(doc, "name", None)
	persisted = (
		frappe.db.get_value("CRM Lead", name, ["lead_owner", "status", "custom_lead_type"], as_dict=True)
		if name
		else None
	)
	if persisted:
		owner, status, lead_type = persisted.lead_owner, persisted.status, persisted.custom_lead_type
	else:
		owner = getattr(doc, "lead_owner", None)
		status = getattr(doc, "status", None)
		lead_type = getattr(doc, "custom_lead_type", None)
	is_unassigned = not owner

	# Calling Team — new-lead inbox: all non-C7 leads incl. ownerless.
	if "Calling Team" in roles and status != "C7":
		return True

	# Marketing — read all assigned leads (write field-locked in validate).
	if "Marketing" in roles and not is_unassigned:
		return True

	# B2F Team (pool) — all assigned leads at a stage-locked status (C7).
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if status in pool_stages and not is_unassigned:
		return True

	# Estimation Team (pool) — all assigned leads with an active QR and no Accepted QR.
	# Accepted is terminal; the UI disables re-requesting once any QR is Accepted.
	# SQL mirror: get_permission_query_conditions Estimation block.
	if roles & QUOTE_SCOPE_ROLES and not is_unassigned:
		if _estimation_qr_gate(name):
			return True

	# Owner-scoped roles (SE/PSE type-locked; Spotter/JSE any-type self;
	# ASM/RSM any-type downstream subtree).
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["lead_type"] and lead_type != rule["lead_type"]:
			continue
		if rule["scope"] == "self" and owner == user:
			return True
		if rule["scope"] == "downstream" and owner in downstream_users(user):
			return True

	# One-up read: an owner-scoped user may also read leads owned by their
	# direct manager (1 level up in CRM Sales Hierarchy), any lead_type.
	# SQL mirror: get_permission_query_conditions one-up clause.
	if roles & set(OWNER_SCOPE_ROLES):
		mgr = upstream_user(user)
		if mgr and owner == mgr:
			return True

	return False


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List-level filter for CRM Lead — the SQL union mirroring :func:`has_permission`.

	Wired via ``permission_query_conditions["CRM Lead"]`` in hooks.py. Returns a
	SQL WHERE clause (or "" for "no filter"). Builds one clause per gate the
	user's roles unlock and OR's them, so it agrees with the single-doc gate
	(round-trip tested). Replaces the legacy "CRM Lead — Permission Query" Server
	Script, which failed under safe_exec (``frappe.get_attr`` not whitelisted).
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return ""

	esc = frappe.db.escape
	clauses: list[str] = []

	# Calling Team — new-lead inbox: all non-C7 leads incl. ownerless.
	if "Calling Team" in roles:
		clauses.append("(`tabCRM Lead`.status != 'C7' OR `tabCRM Lead`.status IS NULL)")

	# Marketing — all assigned leads.
	if "Marketing" in roles:
		clauses.append(_ASSIGNED_ONLY_SQL)

	# B2F Team (pool) — assigned leads at a stage-locked status (C7).
	pool_stages: set[str] = set()
	for role, stages in STAGE_LOCKED.items():
		if role in roles:
			pool_stages |= stages
	if pool_stages:
		stages_sql = ",".join(esc(s) for s in sorted(pool_stages))
		clauses.append(f"(`tabCRM Lead`.status IN ({stages_sql}) AND {_ASSIGNED_ONLY_SQL})")

	# Estimation Team (pool) — assigned leads with an active QR and no accepted QR.
	# Python mirror: _estimation_qr_gate(). Both driven by _ACTIVE_QR_STATUSES / _TERMINAL_QR_STATUS.
	if roles & QUOTE_SCOPE_ROLES:
		qr_statuses_sql = ",".join(esc(s) for s in _ACTIVE_QR_STATUSES)
		active_qr_sql = (
			"EXISTS (SELECT 1 FROM `tabCRM Quote Request` "
			"WHERE `tabCRM Quote Request`.lead = `tabCRM Lead`.name "
			f"AND `tabCRM Quote Request`.status IN ({qr_statuses_sql}))"
		)
		no_accepted_qr_sql = (
			"NOT EXISTS (SELECT 1 FROM `tabCRM Quote Request` "
			"WHERE `tabCRM Quote Request`.lead = `tabCRM Lead`.name "
			f"AND `tabCRM Quote Request`.status = {esc(_TERMINAL_QR_STATUS)})"
		)
		clauses.append(f"({active_qr_sql} AND {no_accepted_qr_sql} AND {_ASSIGNED_ONLY_SQL})")

	# Owner-scoped roles — one clause each, type-locked for SE/PSE.
	downstream: set[str] | None = None
	for role, rule in OWNER_SCOPE_ROLES.items():
		if role not in roles:
			continue
		if rule["scope"] == "self":
			owner_clause = f"`tabCRM Lead`.lead_owner = {esc(user)}"
		else:  # downstream
			if downstream is None:
				downstream = downstream_users(user)
			in_list = ",".join(esc(u) for u in sorted(downstream)) or esc(user)
			owner_clause = f"`tabCRM Lead`.lead_owner IN ({in_list})"
		if rule["lead_type"]:
			clauses.append(f"(`tabCRM Lead`.custom_lead_type = {esc(rule['lead_type'])} AND {owner_clause})")
		else:
			clauses.append(f"({owner_clause})")

	# One-up read — leads owned by the user's direct manager (1 level up), any
	# lead_type. Python mirror: has_permission one-up gate.
	if roles & set(OWNER_SCOPE_ROLES):
		mgr = upstream_user(user)
		if mgr:
			clauses.append(f"`tabCRM Lead`.lead_owner = {esc(mgr)}")

	if not clauses:
		# No role grants any visibility — see nothing.
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
# Tree-scoped lead reassignment guard (ASM / RSM)
# ----------------------------------------------------------------------------
#
# Restricts who an ASM/RSM placed in CRM Sales Hierarchy may hand a lead to:
# their downstream subtree plus their direct manager (1 step up). Orphan or
# freshly-invited users (no hierarchy row) bypass the check so onboarding
# isn't blocked. Tier-1 (System Manager / Sales Head / Sales Coordinator)
# bypass too — they keep cross-team transfer rights.
#
# Enforced in ``CRMLead._check_write_permission`` on a ``lead_owner`` change.
# (The legacy ToDo ``before_insert`` guard was retired with multi-assignee —
# lead ownership is now a single ``lead_owner`` field, so the field-change path
# is the only assignment vector.)
# ----------------------------------------------------------------------------


def upstream_user(user: str) -> str | None:
	"""Return the direct manager's user (1 level up) for ``user`` in
	CRM Sales Hierarchy, or ``None`` if the user has no node or no manager.

	Two cheap lookups — no cache (unlike :func:`downstream_users`, which expands
	a whole subtree). Used by the one-up read gates and by
	:func:`allowed_assignees`."""
	node = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["reports_to"], as_dict=True)
	if not node or not node.reports_to:
		return None
	return frappe.db.get_value("CRM Sales Hierarchy", node.reports_to, "user")


def allowed_assignees(user: str) -> set[str] | None:
	"""Return users ``user`` may set as ``lead_owner``: downstream subtree
	plus direct upline (1 step). Returns ``None`` if ``user`` has no node
	in ``CRM Sales Hierarchy`` — caller skips the check (orphan / newly
	onboarded)."""
	node = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["reports_to"], as_dict=True)
	if not node:
		return None
	allowed = set(downstream_users(user))
	if node.reports_to:
		parent_user = frappe.db.get_value("CRM Sales Hierarchy", node.reports_to, "user")
		if parent_user:
			allowed.add(parent_user)
	return allowed

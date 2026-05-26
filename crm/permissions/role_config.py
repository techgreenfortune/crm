"""Single source of truth for CRM role metadata.

Every role-aware piece of the system reads from this module:

- ``crm/api/session.py`` imports ``ROLE_PRIORITY`` to decide which role label
  to display when a user has multiple roles.
- ``crm/overrides/crm_lead_permissions.py`` imports the access-matrix sets
  (``TIER1_FULL_RW``, ``TIER1_READ_ONLY``, ``FIELD_GATED_RW``, ``STAGE_LOCKED``,
  ``OWNER_SCOPE_ROLES``) to gate ``has_permission`` and
  ``get_permission_query_conditions`` for CRM Lead. ``NO_C7_ROLES`` and
  ``UNASSIGNED_VISIBLE_ROLES`` are conceptual groupings whose members are
  matched by name in the gates (Calling Team gets special unassigned-pool
  visibility; JSE doesn't).
- The frontend (``frontend/src/stores/users.js``) fetches a JSON-serializable
  subset via :func:`get_hierarchy_role_config` to power ``isManager`` /
  ``isSalesUser`` and the Sales Hierarchy tree's ``canDrop`` rule.

Adding/renaming/re-ranking a CRM role is a one-file change here.

ROLE_RANK alignment with the access matrix: RSM is above ASM (per the docstring
in ``crm_lead_permissions.py``), SE/PSE are leaf-level, the tier-1 full-RW roles
sit at the top, and cross-cutting roles (Management, Marketing, Calling/B2F/
Estimation/JSE) sit at the leaf with no subordinates because the tree
isn't their permission gate.
"""

import frappe

# Reporting-tree rank. Lower = more senior. ``canDrop`` in the Hierarchy UI
# blocks moves where ``src.role_rank < tgt.role_rank`` (would put a more-senior
# user under a less-senior one). Same-rank parent-child is allowed by design.
ROLE_RANK: dict[str, int] = {
	"System Manager": 0,
	"Sales Head": 1,
	"Sales Coordinator": 1,
	"RSM": 2,
	"ASM": 3,
	"Sales Executive": 4,
	"Project Sales Executive": 4,
	"Jr. Sales Executive": 4,
	"Management": 4,
	"Marketing": 4,
	"Calling Team": 4,
	"B2F Team": 4,
	"Estimation Team": 4,
}

# Display priority for the User Resource. The most-prestigious role a user
# holds wins when assigning the displayed role. NOT the same as ROLE_RANK —
# ROLE_RANK is reporting-tree position; ROLE_PRIORITY is purely a UI display
# preference (e.g. Marketing comes before ASM here, even though Marketing is
# a leaf role in the hierarchy).
ROLE_PRIORITY: tuple[str, ...] = (
	"System Manager",
	"Sales Head",
	"Sales Coordinator",
	"Management",
	"Marketing",
	"ASM",
	"RSM",
	"Sales Executive",
	"Project Sales Executive",
	"Calling Team",
	"Jr. Sales Executive",
	"B2F Team",
	"Estimation Team",
)

# Access-matrix sets for the CRM Lead 13-role policy. See the docstring in
# ``crm/overrides/crm_lead_permissions.py`` for the full matrix.
TIER1_FULL_RW: frozenset[str] = frozenset({"System Manager", "Sales Head", "Sales Coordinator"})
TIER1_READ_ONLY: frozenset[str] = frozenset({"Management"})
FIELD_GATED_RW: frozenset[str] = frozenset({"Marketing", "B2F Team"})
STAGE_LOCKED: dict[str, frozenset[str]] = {
	"B2F Team": frozenset({"C7"}),
	"Estimation Team": frozenset({"C2"}),
}
NO_C7_ROLES: frozenset[str] = frozenset({"Calling Team", "Jr. Sales Executive"})

# Roles permitted to view leads with no ``lead_owner`` (the unassigned pool).
# Tier-1 full-RW already see every lead; Calling Team owns the unassigned
# inbox because they create + first-touch new leads. Every other role
# (Management, Marketing, B2F, Estimation, JSE, SE/PSE/ASM/RSM) sees only
# assigned leads. Owner-scoped roles are unaffected — their query already
# filters by ``lead_owner``, which naturally excludes unassigned rows.
UNASSIGNED_VISIBLE_ROLES: frozenset[str] = TIER1_FULL_RW | frozenset({"Calling Team"})

# Owner-scoped roles. ``scope`` is ``"self"`` (owner = user) or ``"downstream"``
# (owner in the user's CRM Sales Hierarchy subtree). ``lead_type`` restricts the
# rule to a specific ``custom_lead_type`` value or is ``None`` for any-type.
OWNER_SCOPE_ROLES: dict[str, dict] = {
	"Sales Executive": {"scope": "self", "lead_type": "Retail"},
	"Project Sales Executive": {"scope": "self", "lead_type": "Projects"},
	"ASM": {"scope": "downstream", "lead_type": None},
	"RSM": {"scope": "downstream", "lead_type": None},
}

# Downstream-scoped roles — subject to the tree-scoped assignment rule
# (``crm.overrides.crm_lead_permissions.guard_lead_assignment``). Derived from
# OWNER_SCOPE_ROLES so adding a new downstream role is a one-line change above.
DOWNSTREAM_SCOPE_ROLES: frozenset[str] = frozenset(
	r for r, cfg in OWNER_SCOPE_ROLES.items() if cfg["scope"] == "downstream"
)

# Roles that occupy non-leaf positions in CRM Sales Hierarchy: tier-1 sits at
# the root, downstream-scoped roles (RSM / ASM) sit in the middle with reports
# below them. Used by ``crm.api.user.update_user_role`` to block demoting a
# user with active hierarchy responsibility (root node or has direct reports)
# to a non-managerial profile. System Manager is in the set too but never
# reaches the gate — it's handled out-of-band in update_user_role.
MANAGERIAL_ROLES: frozenset[str] = TIER1_FULL_RW | TIER1_READ_ONLY | DOWNSTREAM_SCOPE_ROLES


@frappe.whitelist()
def get_hierarchy_role_config() -> dict:
	"""Returns the role metadata the frontend needs.

	``tier1_full_rw`` is the sorted list form so the JSON payload is stable.
	Other access-matrix sets (``STAGE_LOCKED`` / ``OWNER_SCOPE_ROLES`` / etc.)
	stay backend-only — the frontend doesn't make policy decisions, it just
	gates UI elements on tier-1 membership and reads ``ROLE_RANK`` for the
	Sales Hierarchy tree.
	"""
	# Gate: matches the pattern in crm/api/session.py — non-CRM users (and
	# Guest) get a PermissionError instead of a peek at the role topology.
	from crm.api.session import get_session_role_flags

	get_session_role_flags()
	return {
		"role_rank": ROLE_RANK,
		"tier1_full_rw": sorted(TIER1_FULL_RW),
	}

# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from crm.permissions.role_config import (
	FIELD_GATED_RW,
	OWNER_SCOPE_ROLES,
	QUOTE_REVERT_LOCKED_STAGES,
	QUOTE_REVERT_ROLES,
	REVIEWER_ROLES,
	ROLE_PRIORITY,
	ROLE_RANK,
	STAGE_LOCKED,
	TIER1_FULL_RW,
	get_hierarchy_role_config,
)

EXPECTED_ROLES = frozenset(
	{
		"System Manager",
		"Sales Head",
		"Sales Coordinator",
		"RSM",
		"ASM",
		"Sales Executive",
		"Project Sales Executive",
		"Spotter",
		"Jr. Sales Executive",
		"Management",
		"Marketing",
		"Calling Team",
		"B2F Team",
		"Estimation Team",
	}
)


class TestRoleConfigStructure(IntegrationTestCase):
	"""Static assertions on the role-metadata module. No DB writes."""

	def test_role_rank_has_expected_roles(self):
		self.assertEqual(set(ROLE_RANK.keys()), EXPECTED_ROLES)
		self.assertEqual(len(ROLE_RANK), 14)

	def test_role_priority_covers_every_role_exactly_once(self):
		self.assertEqual(set(ROLE_PRIORITY), EXPECTED_ROLES)
		self.assertEqual(len(ROLE_PRIORITY), len(set(ROLE_PRIORITY)))

	def test_tier_sets_are_subsets_of_role_rank(self):
		known = set(ROLE_RANK.keys())
		for name, bucket in [
			("TIER1_FULL_RW", TIER1_FULL_RW),
			("FIELD_GATED_RW", FIELD_GATED_RW),
			("STAGE_LOCKED.keys()", set(STAGE_LOCKED.keys())),
			("OWNER_SCOPE_ROLES.keys()", set(OWNER_SCOPE_ROLES.keys())),
		]:
			self.assertTrue(
				set(bucket).issubset(known),
				f"{name} contains roles not in ROLE_RANK: {set(bucket) - known}",
			)

	def test_tier1_and_owner_scope_are_disjoint(self):
		"""A role can't simultaneously be tier-1 full-RW and owner-scoped."""
		self.assertFalse(TIER1_FULL_RW & set(OWNER_SCOPE_ROLES.keys()))

	def test_owner_scope_rules_are_well_formed(self):
		for role, rule in OWNER_SCOPE_ROLES.items():
			self.assertIn(rule["scope"], {"self", "downstream"}, msg=role)
			self.assertIn("lead_type", rule, msg=role)


class TestRoleConfigAPI(IntegrationTestCase):
	"""The whitelisted endpoint must (a) return the expected shape and
	(b) gate non-CRM callers via PermissionError."""

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_returns_expected_shape_for_admin(self):
		frappe.set_user("Administrator")
		payload = get_hierarchy_role_config()
		self.assertEqual(
			set(payload.keys()),
			{"role_rank", "tier1_full_rw", "reviewer_roles", "telephony_agent_manager_roles"},
		)
		self.assertEqual(payload["role_rank"], ROLE_RANK)
		self.assertEqual(payload["tier1_full_rw"], sorted(TIER1_FULL_RW))
		self.assertEqual(payload["reviewer_roles"], sorted(REVIEWER_ROLES))

	def test_guest_is_denied(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_hierarchy_role_config()


class TestQuoteRevertRoles(IntegrationTestCase):
	def test_revert_roles_are_managers_only(self):
		self.assertEqual(
			QUOTE_REVERT_ROLES,
			{"System Manager", "Sales Head", "Sales Coordinator"},
		)

	def test_terminal_stages_locked_but_c7_open(self):
		self.assertEqual(QUOTE_REVERT_LOCKED_STAGES, {"C4", "C6"})

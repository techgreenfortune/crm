from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.permissions.telephony_agent import (
	get_permission_query_conditions,
	has_permission,
	is_telephony_manager,
	validate_self_edit,
)

ROLES = "crm.permissions.telephony_agent.frappe.get_roles"
STORED_USER = "crm.permissions.telephony_agent.frappe.db.get_value"


def agent_doc(user="agent@example.com", new=False, **fields):
	doc = frappe.new_doc("CRM Telephony Agent")
	doc.update({"user": user, "mobile_no": "9000000001", "exotel_number": "04000000001", **fields})
	if not new:
		doc.name = user
		doc.set("__islocal", 0)
	return doc


class TestTelephonyAgentPermissions(FrappeTestCase):
	def test_managers_come_from_role_config(self):
		with patch(ROLES, return_value=["Sales Head"]):
			self.assertTrue(is_telephony_manager("head@example.com"))
		for roles in (["Sales User"], ["Sales User", "ASM"], ["RSM"], ["Management"]):
			with patch(ROLES, return_value=roles):
				self.assertFalse(is_telephony_manager("agent@example.com"), roles)
		self.assertTrue(is_telephony_manager("Administrator"))

	def test_agents_only_list_their_own_record(self):
		with patch(ROLES, return_value=["Sales User"]):
			condition = get_permission_query_conditions("agent@example.com")
		self.assertIn("`tabCRM Telephony Agent`.`user` = 'agent@example.com'", condition)
		with patch(ROLES, return_value=["Sales Coordinator"]):
			self.assertEqual(get_permission_query_conditions("coord@example.com"), "")

	def test_agents_can_open_only_their_own_record(self):
		# Build docs before mocking db.get_value: new_doc reads DocType metadata through it.
		doc, new_doc = agent_doc(), agent_doc(new=True)
		with patch(STORED_USER, return_value="agent@example.com"):
			with patch(ROLES, return_value=["Sales User"]):
				self.assertTrue(has_permission(doc, "read", "agent@example.com"))
				self.assertFalse(has_permission(doc, "write", "other@example.com"))
				self.assertFalse(has_permission(new_doc, "create", "agent@example.com"))
				self.assertFalse(has_permission(doc, "delete", "agent@example.com"))
			with patch(ROLES, return_value=["Sales Head"]):
				self.assertTrue(has_permission(doc, "write", "head@example.com"))

	def test_stored_owner_decides_not_the_submitted_one(self):
		doc = agent_doc(user="agent@example.com")
		with patch(STORED_USER, return_value="other@example.com"), patch(ROLES, return_value=["Sales User"]):
			self.assertFalse(has_permission(doc, "write", "agent@example.com"))


class TestTelephonyAgentSelfEdit(FrappeTestCase):
	def _validate(self, doc, before, roles=("Sales User",)):
		with (
			patch(ROLES, return_value=list(roles)),
			patch.dict(frappe.session, {"user": "agent@example.com"}),
			patch.object(doc, "get_doc_before_save", return_value=before),
		):
			validate_self_edit(doc)

	def test_agent_may_change_their_calling_preference(self):
		self._validate(agent_doc(default_medium="Exotel"), agent_doc(default_medium="Twilio"))

	def test_agent_may_not_change_phone_routing(self):
		for field, value in (
			("mobile_no", "9000000009"),
			("exotel_number", "04000000009"),
			("exotel_softphone_enabled", 1),
			("user", "other@example.com"),
		):
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				self._validate(agent_doc(**{field: value}), agent_doc())

	def test_agent_may_not_create_a_record(self):
		with self.assertRaises(frappe.PermissionError):
			self._validate(agent_doc(new=True), None)

	def test_managers_and_trusted_server_saves_are_not_limited(self):
		self._validate(agent_doc(mobile_no="9000000009"), agent_doc(), roles=("Sales Head",))
		doc = agent_doc(mobile_no="9000000009")
		doc.flags.ignore_permissions = True
		self._validate(doc, agent_doc())


class TestTelephonyAgentDirectSave(FrappeTestCase):
	"""Saves a real record as a Sales User, the way a direct frappe.client call would."""

	def setUp(self):
		self.user = "tp-agent@example.com"
		if not frappe.db.exists("User", self.user):
			frappe.get_doc(
				{"doctype": "User", "email": self.user, "first_name": "TP Agent", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", self.user).add_roles("Sales User")
		# Reuse a record left by an earlier run: something in the save path commits.
		if frappe.db.exists("CRM Telephony Agent", self.user):
			agent = frappe.get_doc("CRM Telephony Agent", self.user)
		else:
			agent = frappe.new_doc("CRM Telephony Agent")
			agent.user = self.user
		agent.update({"mobile_no": "9000000001", "default_medium": "", "phone_nos": []})
		agent.save(ignore_permissions=True)
		frappe.set_user(self.user)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_agent_cannot_change_their_number_through_phone_nos(self):
		doc = frappe.get_doc("CRM Telephony Agent", self.user)
		doc.phone_nos[0].is_primary = 0
		doc.append("phone_nos", {"number": "9000000009", "is_primary": 1})

		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(frappe.db.get_value("CRM Telephony Agent", self.user, "mobile_no"), "9000000001")

	def test_agent_can_still_save_their_default_medium(self):
		doc = frappe.get_doc("CRM Telephony Agent", self.user)
		doc.default_medium = "Exotel"
		doc.save()

		self.assertEqual(frappe.db.get_value("CRM Telephony Agent", self.user, "default_medium"), "Exotel")

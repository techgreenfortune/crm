from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_lead, make_user


def _silent_enqueue(*_args, **_kwargs):
	pass


class TestAcceptedQuoteRevert(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._patches = [
			patch("frappe.enqueue", side_effect=_silent_enqueue),
			patch("frappe.utils.safe_exec.safe_enqueue", side_effect=_silent_enqueue),
		]
		for p in self._patches:
			p.start()
		self.lead = make_lead(phone="+919800000777")

	def tearDown(self):
		for p in self._patches:
			p.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _accepted_qr(self):
		qr = frappe.new_doc("CRM Quote Request")
		qr.update(
			{
				"lead": self.lead.name,
				"status": "Accepted",
				"quote_number": "TRA-QT-1",
				"quote_value": 100,
				"quote_margin": 10,
				"quote_sq_ft": 50,
				"total_quantity": 1,
			}
		)
		qr.flags.ignore_mandatory = True
		qr.insert(ignore_permissions=True)
		return qr

	def _as(self, email, role):
		make_user(email=email, roles=[role])
		frappe.set_user(email)

	def _move_back(self, qr, status="Quote Received"):
		qr = frappe.get_doc("CRM Quote Request", qr.name)
		qr.status = status
		qr.save(ignore_permissions=True)
		return qr

	def test_manager_can_move_back_on_open_stage(self):
		qr = self._accepted_qr()
		self._as("qr-head@example.com", "Sales Head")
		self.assertEqual(self._move_back(qr).status, "Quote Received")

	def test_c7_is_not_locked(self):
		qr = self._accepted_qr()
		frappe.db.set_value("CRM Lead", self.lead.name, "status", "C7")
		self._as("qr-head2@example.com", "Sales Coordinator")
		self.assertEqual(self._move_back(qr).status, "Quote Received")

	def test_terminal_stages_block_move_back(self):
		qr = self._accepted_qr()
		self._as("qr-head3@example.com", "Sales Head")
		for stage in ("C4", "C6"):
			frappe.db.set_value("CRM Lead", self.lead.name, "status", stage)
			with self.assertRaises(frappe.ValidationError):
				self._move_back(qr)

	def test_non_managers_blocked(self):
		qr = self._accepted_qr()
		for i, role in enumerate(("Management", "ASM", "Estimation Team")):
			self._as(f"qr-other{i}@example.com", role)
			with self.assertRaises(frappe.PermissionError):
				self._move_back(qr)

	def test_pending_is_not_a_valid_target(self):
		qr = self._accepted_qr()
		self._as("qr-head4@example.com", "Sales Head")
		with self.assertRaises(frappe.ValidationError):
			self._move_back(qr, "Pending")

	def test_accepted_quote_fields_stay_frozen(self):
		qr = self._accepted_qr()
		self._as("qr-head5@example.com", "Sales Head")
		qr = frappe.get_doc("CRM Quote Request", qr.name)
		qr.quote_value = 999
		with self.assertRaises(frappe.ValidationError):
			qr.save(ignore_permissions=True)

	def test_manager_can_request_revision_with_reason(self):
		qr = self._accepted_qr()
		self._as("qr-head7@example.com", "Sales Head")
		qr = frappe.get_doc("CRM Quote Request", qr.name)
		qr.status = "Revision Requested"
		qr.notes = "Customer wants a different finish"
		qr.save(ignore_permissions=True)

		self.assertTrue(frappe.db.get_value("CRM Quote Request", qr.name, "is_superseded"))
		self.assertTrue(
			frappe.db.exists(
				"CRM Quote Request",
				{"lead": self.lead.name, "status": "Pending", "notes": "Customer wants a different finish"},
			)
		)

	def test_notes_stay_frozen_when_moving_back_to_quote_received(self):
		qr = self._accepted_qr()
		self._as("qr-head8@example.com", "Sales Head")
		qr = frappe.get_doc("CRM Quote Request", qr.name)
		qr.status = "Quote Received"
		qr.notes = "sneaky edit"
		with self.assertRaises(frappe.ValidationError):
			qr.save(ignore_permissions=True)

	def test_move_back_does_not_resync_lead(self):
		qr = self._accepted_qr()
		frappe.db.set_value("CRM Lead", self.lead.name, "custom_tentative_value", 1)
		self._as("qr-head6@example.com", "Sales Head")
		self._move_back(qr)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead.name, "custom_tentative_value"), 1)
		self.assertFalse(
			frappe.db.exists("CRM Task", {"reference_docname": self.lead.name, "task_type": "review_quote"})
		)

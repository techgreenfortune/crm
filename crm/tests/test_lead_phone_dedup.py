"""Controller-level phone dedup tests (crm_lead.py `_dedup_and_normalize_mobile`).

Regression for the +91 / 91 / national-format duplicate gap: a lead saved via the
Desk/controller path with a country-code prefix must match an existing lead whose
``mobile_no`` is stored in any other Indian format (national, 0-prefix, etc.), not
only an exact E164 match.

Gotcha: ``_dedup_and_normalize_mobile`` short-circuits when ``frappe.flags.in_test``
is set (always true under FrappeTestCase). Tests insert the *existing* row with the
flag on (stored un-normalized, mimicking an import), then clear the flag only around
the duplicate attempt so the dedup actually runs.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_lead


class TestLeadPhoneDedup(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _insert_with_dedup(self, phone: str):
		"""Insert a lead with the in_test guard cleared so dedup/normalize runs."""
		original = frappe.flags.in_test
		frappe.flags.in_test = False
		try:
			return make_lead(phone=phone, first_name="Dup", last_name="Attempt")
		finally:
			frappe.flags.in_test = original

	def test_existing_national_blocks_e164_submission(self):
		"""Reported case: existing stored without +91, new lead with +91 → blocked."""
		make_lead(phone="9876543210")  # in_test on → stored raw national

		with self.assertRaises(frappe.ValidationError):
			self._insert_with_dedup("+919876543210")

	def test_existing_national_blocks_91_prefix_submission(self):
		"""New lead with bare 91 prefix also matches the national-stored row."""
		make_lead(phone="9876543210")

		with self.assertRaises(frappe.ValidationError):
			self._insert_with_dedup("919876543210")

	def test_existing_e164_blocks_national_submission(self):
		"""Reverse direction: existing stored as E164, new lead national → blocked."""
		make_lead(phone="+919876543210")

		with self.assertRaises(frappe.ValidationError):
			self._insert_with_dedup("9876543210")

	def test_distinct_number_not_blocked(self):
		"""A genuinely different number still inserts cleanly."""
		make_lead(phone="9876543210")

		lead = self._insert_with_dedup("9123456780")
		self.assertEqual(lead.mobile_no, "+919123456780")

	def test_bulk_import_still_dedups(self):
		"""Regression: dedup must run during Data Import (in_import no longer bypasses)."""
		make_lead(phone="9876543210")

		original_test = frappe.flags.in_test
		original_import = frappe.flags.in_import
		frappe.flags.in_test = False
		frappe.flags.in_import = True
		try:
			with self.assertRaises(frappe.ValidationError):
				make_lead(phone="+919876543210", first_name="Import", last_name="Dup")
		finally:
			frappe.flags.in_test = original_test
			frappe.flags.in_import = original_import

	def test_bulk_import_normalizes_mobile(self):
		"""Import path also E164-normalizes so stored rows stay canonical."""
		original_test = frappe.flags.in_test
		original_import = frappe.flags.in_import
		frappe.flags.in_test = False
		frappe.flags.in_import = True
		try:
			lead = make_lead(phone="9123456780", first_name="Import", last_name="Norm")
		finally:
			frappe.flags.in_test = original_test
			frappe.flags.in_import = original_import
		self.assertEqual(lead.mobile_no, "+919123456780")

	def test_closed_lost_lead_allows_new_linked_lead(self):
		old = make_lead(phone="9876543210", status="C6", lost_reason="Chose Competitor")

		lead = self._insert_with_dedup("+919876543210")
		self.assertEqual(lead.custom_previous_lead, old.name)

	def test_closed_won_lead_allows_new_linked_lead(self):
		old = make_lead(phone="9876543210", status="C4")

		lead = self._insert_with_dedup("+919876543210")
		self.assertEqual(lead.custom_previous_lead, old.name)

	def test_open_lead_blocks_even_with_closed_history(self):
		make_lead(phone="9876543210", status="C6", lost_reason="Chose Competitor")
		self._insert_with_dedup("+919876543210")

		with self.assertRaises(frappe.ValidationError):
			self._insert_with_dedup("+919876543210")

	def test_on_hold_c7_lead_blocks(self):
		make_lead(phone="9876543210", status="C7")

		with self.assertRaises(frappe.ValidationError):
			self._insert_with_dedup("+919876543210")

	def test_reopening_closed_lead_blocked_while_another_is_open(self):
		old = make_lead(phone="9876543210", status="C6", lost_reason="Chose Competitor")
		self._insert_with_dedup("+919876543210")

		old.reload()
		old.status = "C1"
		original = frappe.flags.in_test
		frappe.flags.in_test = False
		try:
			with self.assertRaises(frappe.ValidationError):
				old.save()
		finally:
			frappe.flags.in_test = original

	def test_closed_lead_insert_allowed_alongside_open_lead(self):
		"""Historical import of a Lost lead must not be blocked by the number's live lead."""
		make_lead(phone="9876543210")

		original = frappe.flags.in_test
		frappe.flags.in_test = False
		try:
			lead = make_lead(phone="+919876543210", status="C6", lost_reason="Chose Competitor")
		finally:
			frappe.flags.in_test = original
		self.assertEqual(lead.status, "C6")
		self.assertFalse(lead.custom_previous_lead)

	def test_mobile_change_links_previous_closed_lead(self):
		old = make_lead(phone="9876543210", status="C6", lost_reason="Chose Competitor")
		lead = self._insert_with_dedup("9123456780")
		self.assertFalse(lead.custom_previous_lead)

		lead.mobile_no = "+919876543210"
		original = frappe.flags.in_test
		frappe.flags.in_test = False
		try:
			lead.save()
		finally:
			frappe.flags.in_test = original
		self.assertEqual(lead.custom_previous_lead, old.name)

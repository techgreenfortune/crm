# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the CRM Lead / CRM Deal doc-event handlers.

Covers: the enqueue-skip when sync is unconfigured, skipping rows with no
matchable contact info, failure logging going to exactly one place (Failed
Meta Sync Log, not a duplicate Error Log entry too), the has_value_changed
guard on Won re-saves, and — the important one — that the removal row is
built from the linked Lead's fields, not the Deal's own (possibly edited,
possibly stale) fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.handlers import (
	on_deal_won,
	on_lead_created,
	sync_add_lead,
	sync_remove_deal,
)


class TestOnLeadCreated(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	@patch("crm.integrations.meta.handlers.get_settings", return_value=None)
	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	def test_does_not_enqueue_when_sync_unconfigured(self, mock_enqueue, _mock_settings):
		on_lead_created(MagicMock(name="CRM-LEAD-0001"))
		mock_enqueue.assert_not_called()

	@patch("crm.integrations.meta.handlers.get_settings", return_value=MagicMock())
	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	def test_enqueues_when_sync_configured(self, mock_enqueue, _mock_settings):
		doc = MagicMock()
		doc.name = "CRM-LEAD-0001"
		on_lead_created(doc)
		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args.kwargs["lead_name"], "CRM-LEAD-0001")


class TestOnDealWon(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _doc(self, status: str, status_changed: bool):
		doc = MagicMock()
		doc.name = "CRM-DEAL-0001"
		doc.status = status
		doc.has_value_changed.return_value = status_changed
		return doc

	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	def test_skips_when_status_field_itself_unchanged(self, mock_enqueue):
		# Re-saving an already-Won deal (e.g. editing an unrelated field) must
		# not re-fire the removal call every single save.
		on_deal_won(self._doc("Won", status_changed=False))
		mock_enqueue.assert_not_called()

	@patch("crm.integrations.meta.handlers.get_settings", return_value=MagicMock())
	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	@patch("crm.integrations.meta.handlers._deal_status_type", return_value="Won")
	def test_enqueues_when_moved_to_won(self, _mock_type, mock_enqueue, _mock_settings):
		on_deal_won(self._doc("Won", status_changed=True))
		mock_enqueue.assert_called_once()

	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	@patch("crm.integrations.meta.handlers._deal_status_type", return_value="Ongoing")
	def test_skips_when_new_status_is_not_won(self, _mock_type, mock_enqueue):
		on_deal_won(self._doc("Negotiation", status_changed=True))
		mock_enqueue.assert_not_called()

	@patch("crm.integrations.meta.handlers.get_settings", return_value=None)
	@patch("crm.integrations.meta.handlers.frappe.enqueue")
	@patch("crm.integrations.meta.handlers._deal_status_type", return_value="Won")
	def test_skips_when_sync_unconfigured_even_if_won(self, _mock_type, mock_enqueue, _mock_settings):
		on_deal_won(self._doc("Won", status_changed=True))
		mock_enqueue.assert_not_called()


class TestSyncAddLead(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _make_lead(self, **kwargs):
		defaults = {"doctype": "CRM Lead", "first_name": "Test"}
		defaults.update(kwargs)
		return frappe.get_doc(defaults).insert(ignore_permissions=True)

	@patch("crm.integrations.meta.handlers.add_users_to_audience")
	def test_skips_row_with_no_email_and_no_phone(self, mock_add):
		lead = self._make_lead(first_name="NoContact")
		sync_add_lead(lead.name)
		mock_add.assert_not_called()

	@patch("crm.integrations.meta.handlers.add_users_to_audience")
	def test_sends_row_when_email_present(self, mock_add):
		lead = self._make_lead(first_name="HasEmail", email="has@email.com")
		sync_add_lead(lead.name)
		mock_add.assert_called_once()

	@patch("crm.integrations.meta.handlers.log_sync_failure")
	@patch("crm.integrations.meta.handlers.add_users_to_audience", side_effect=Exception("boom"))
	def test_logs_failure_exactly_once_on_exception(self, _mock_add, mock_log_failure):
		lead = self._make_lead(first_name="WillFail", email="fails@email.com")
		# Must not raise back into the caller (this runs in a background job).
		sync_add_lead(lead.name)
		mock_log_failure.assert_called_once()
		self.assertEqual(mock_log_failure.call_args[0][0], "Add")
		self.assertEqual(mock_log_failure.call_args[0][1], "CRM Lead")
		self.assertEqual(mock_log_failure.call_args[0][2], lead.name)


class TestSyncRemoveDeal(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _make_lead(self, **kwargs):
		defaults = {"doctype": "CRM Lead", "first_name": "Test"}
		defaults.update(kwargs)
		return frappe.get_doc(defaults).insert(ignore_permissions=True)

	def _make_deal(self, *, email=None, mobile_no=None, **kwargs):
		# CRM Deal.email/.mobile_no are derived from the primary row in its
		# `contacts` child table (see set_primary_email_mobile_no) — they
		# can't be set directly on the Deal doc itself, so route them
		# through a primary contact row instead, as a real Deal would have.
		defaults = {"doctype": "CRM Deal", "status": "Qualification"}
		defaults.update(kwargs)
		deal = frappe.get_doc(defaults)
		if email or mobile_no:
			deal.append("contacts", {"email": email or "", "mobile_no": mobile_no or "", "is_primary": 1})
		return deal.insert(ignore_permissions=True)

	@patch("crm.integrations.meta.handlers.remove_users_from_audience")
	@patch("crm.integrations.meta.handlers.build_user_row")
	def test_uses_linked_lead_fields_not_deal_fields(self, mock_build_row, mock_remove):
		# The Add call hashed the Lead's fields — if the rep then edits the
		# email/phone directly on the Deal, the Remove call must still hash
		# the *Lead's* fields (what's actually in the audience), or the
		# hashes won't match and the DELETE silently removes nobody.
		lead = self._make_lead(
			first_name="LeadFirst", last_name="LeadLast", email="lead@example.com", mobile_no="9876543210"
		)
		deal = self._make_deal(
			lead=lead.name,
			first_name="DealFirst",
			last_name="DealLast",
			email="deal@example.com",
			mobile_no="9876543211",
		)
		mock_build_row.return_value = ["irrelevant"]
		sync_remove_deal(deal.name)
		mock_build_row.assert_called_once_with(
			email="lead@example.com",
			phone="9876543210",
			first_name="LeadFirst",
			last_name="LeadLast",
			country=None,
		)
		mock_remove.assert_called_once()

	@patch("crm.integrations.meta.handlers.remove_users_from_audience")
	@patch("crm.integrations.meta.handlers.build_user_row")
	def test_falls_back_to_deal_fields_when_no_linked_lead(self, mock_build_row, mock_remove):
		deal = self._make_deal(
			first_name="DealFirst", last_name="DealLast", email="deal@example.com", mobile_no="9876543211"
		)
		mock_build_row.return_value = ["irrelevant"]
		sync_remove_deal(deal.name)
		mock_build_row.assert_called_once_with(
			email="deal@example.com",
			phone="9876543211",
			first_name="DealFirst",
			last_name="DealLast",
			country=None,
		)
		mock_remove.assert_called_once()

	@patch("crm.integrations.meta.handlers.remove_users_from_audience")
	def test_skips_when_neither_lead_nor_deal_has_contact_info(self, mock_remove):
		deal = self._make_deal(first_name="NoContact")
		sync_remove_deal(deal.name)
		mock_remove.assert_not_called()

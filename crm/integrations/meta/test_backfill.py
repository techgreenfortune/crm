# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Pure-logic tests for the Meta Audience backfill script's batching.

The Won-skip / DB-reading parts were verified with a live dry run against
the dev site's real CRM Lead/Deal data (see developer notes) rather than
fixtures here, to avoid mutating that data; this covers the chunking
boundary cases, including batching a generator (not just a list), since
_leads_to_sync now streams leads page-by-page instead of returning a list.
"""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.backfill import _chunks, enqueue_backfill, run_backfill


class TestChunks(FrappeTestCase):
	def test_splits_into_even_batches(self):
		items = list(range(10))
		self.assertEqual(list(_chunks(items, 3)), [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])

	def test_batch_size_larger_than_items_returns_one_batch(self):
		items = list(range(5))
		self.assertEqual(list(_chunks(items, 10000)), [items])

	def test_empty_items_yields_no_batches(self):
		self.assertEqual(list(_chunks([], 100)), [])

	def test_chunks_a_generator_not_just_a_list(self):
		# run_backfill now passes _leads_to_sync()'s generator directly to
		# _chunks (streamed, not materialized into a list first) — make sure
		# chunking still works correctly against a plain iterator.
		def gen():
			yield from range(7)

		self.assertEqual(list(_chunks(gen(), 3)), [[0, 1, 2], [3, 4, 5], [6]])


class _FakeSettings:
	enabled = True


class TestRunBackfill(FrappeTestCase):
	"""run_backfill tests, isolated from real DB data by patching the lead
	source (_iter_all_leads) and won-lookup (_won_lead_names) directly —
	keeps these deterministic and independent of whatever leads/deals
	already exist on the dev site these tests run against."""

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _leads(self):
		return [
			{
				"name": "L-WON",
				"email": "won@x.com",
				"mobile_no": "9876500001",
				"first_name": "A",
				"last_name": "A",
			},
			{"name": "L-NOCONTACT", "email": None, "mobile_no": None, "first_name": "B", "last_name": "B"},
			{
				"name": "L-ELIGIBLE-1",
				"email": "e1@x.com",
				"mobile_no": "9876500002",
				"first_name": "C",
				"last_name": "C",
			},
			{
				"name": "L-ELIGIBLE-2",
				"email": "e2@x.com",
				"mobile_no": "9876500003",
				"first_name": "D",
				"last_name": "D",
			},
		]

	@patch("crm.integrations.meta.backfill.get_settings", return_value=None)
	def test_raises_when_unconfigured_and_not_dry_run(self, _mock_settings):
		with self.assertRaises(frappe.ValidationError):
			run_backfill(dry_run=False)

	@patch("crm.integrations.meta.backfill.get_settings", return_value=None)
	@patch("crm.integrations.meta.backfill._iter_all_leads")
	@patch("crm.integrations.meta.backfill._won_lead_names", return_value={"L-WON"})
	def test_dry_run_does_not_require_settings_or_call_api(self, _mock_won, mock_iter, _mock_settings):
		mock_iter.return_value = iter(self._leads())
		with patch("crm.integrations.meta.backfill.add_users_to_audience") as mock_add:
			summary = run_backfill(dry_run=True)
			mock_add.assert_not_called()
		self.assertEqual(summary["skipped_won"], 1)
		self.assertEqual(summary["skipped_no_contact"], 1)
		self.assertEqual(summary["synced"], 2)

	@patch("crm.integrations.meta.backfill.get_settings", return_value=_FakeSettings())
	@patch("crm.integrations.meta.backfill._iter_all_leads")
	@patch("crm.integrations.meta.backfill._won_lead_names", return_value={"L-WON"})
	@patch("crm.integrations.meta.backfill.add_users_to_audience")
	def test_skips_won_and_no_contact_leads(self, mock_add, mock_won, mock_iter, _mock_settings):
		mock_iter.return_value = iter(self._leads())
		summary = run_backfill(batch_size=10, dry_run=False)
		# Only the two eligible leads should ever reach the API call.
		synced_rows = mock_add.call_args[0][0]
		self.assertEqual(len(synced_rows), 2)
		self.assertEqual(summary["skipped_won"], 1)
		self.assertEqual(summary["skipped_no_contact"], 1)
		self.assertEqual(summary["synced"], 2)

	@patch("crm.integrations.meta.backfill.get_settings", return_value=_FakeSettings())
	@patch("crm.integrations.meta.backfill._iter_all_leads")
	@patch("crm.integrations.meta.backfill._won_lead_names", return_value=set())
	@patch("crm.integrations.meta.backfill.add_users_to_audience", side_effect=Exception("meta down"))
	def test_failed_batch_logs_one_retryable_row_per_lead(
		self, _mock_add, _mock_won, mock_iter, _mock_settings
	):
		# Failed Meta Sync Log.entity_name is a Dynamic Link — it must point to
		# a real CRM Lead, so use actual inserted leads rather than made-up names.
		lead1 = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "C",
				"last_name": "C",
				"email": "e1@x.com",
				"mobile_no": "9876500002",
			}
		).insert(ignore_permissions=True)
		lead2 = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "D",
				"last_name": "D",
				"email": "e2@x.com",
				"mobile_no": "9876500003",
			}
		).insert(ignore_permissions=True)
		mock_iter.return_value = iter(
			[
				{
					"name": lead1.name,
					"email": lead1.email,
					"mobile_no": lead1.mobile_no,
					"first_name": lead1.first_name,
					"last_name": lead1.last_name,
				},
				{
					"name": lead2.name,
					"email": lead2.email,
					"mobile_no": lead2.mobile_no,
					"first_name": lead2.first_name,
					"last_name": lead2.last_name,
				},
			]
		)
		before = frappe.db.count("Failed Meta Sync Log")
		summary = run_backfill(batch_size=10, dry_run=False)
		after = frappe.db.count("Failed Meta Sync Log")
		self.assertEqual(after - before, 2)
		self.assertEqual(summary["failed"], 2)
		self.assertEqual(summary["synced"], 0)
		logged_names = frappe.get_all(
			"Failed Meta Sync Log",
			filters={"entity_name": ["in", [lead1.name, lead2.name]]},
			pluck="entity_name",
		)
		self.assertEqual(sorted(logged_names), sorted([lead1.name, lead2.name]))


class TestEnqueueBackfill(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	@patch("crm.integrations.meta.backfill.frappe.enqueue")
	def test_enqueues_on_long_queue(self, mock_enqueue):
		enqueue_backfill(batch_size=500, dry_run=True)
		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(mock_enqueue.call_args.kwargs["batch_size"], 500)
		self.assertEqual(mock_enqueue.call_args.kwargs["dry_run"], True)

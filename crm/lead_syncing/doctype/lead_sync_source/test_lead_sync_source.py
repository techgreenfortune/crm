# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""FacebookSyncSource lead-tagging tests.

Covers the Meta Ads intake fix: synced leads must be tagged
source="Paid" (not the retired "Facebook" CRM Lead Source, which no
longer exists and would fail Link validation on insert), with
custom_sub_source defaulting to "Meta Generic" unless the Lead Sync
Source config overrides it.
"""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.lead_syncing.doctype.lead_sync_source.facebook import FacebookSyncSource

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]

_QUESTION_MAP = {"full_name": "first_name", "phone_number": "mobile_no"}


def _fb_lead(lead_id: str, full_name: str, phone: str) -> dict:
	return {
		"id": lead_id,
		"field_data": [
			{"name": "full_name", "values": [full_name]},
			{"name": "phone_number", "values": [phone]},
		],
	}


class TestFacebookSyncSourceLeadTagging(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._mapping_patch = patch.object(
			FacebookSyncSource, "get_form_questions_mapping", return_value=_QUESTION_MAP
		)
		self._mapping_patch.start()

	def tearDown(self):
		self._mapping_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_synced_lead_tagged_paid_meta_generic_by_default(self):
		source = FacebookSyncSource("token", "form-fb-1")
		doc = source.sync_single_lead(_fb_lead("fb-lead-1", "Meta One", "+919812346001"))
		self.assertEqual(doc.source, "Paid")
		self.assertEqual(doc.get("custom_sub_source"), "Meta Generic")
		self.assertEqual(doc.get("facebook_lead_id"), "fb-lead-1")
		self.assertEqual(doc.get("facebook_form_id"), "form-fb-1")

	def test_synced_lead_uses_configured_sub_source(self):
		source = FacebookSyncSource("token", "form-fb-2", sub_source="Meta Remarketing")
		doc = source.sync_single_lead(_fb_lead("fb-lead-2", "Meta Two", "+919812346002"))
		self.assertEqual(doc.source, "Paid")
		self.assertEqual(doc.get("custom_sub_source"), "Meta Remarketing")

	def test_duplicate_lead_logged_not_raised(self):
		source = FacebookSyncSource("token", "form-fb-3")
		source.sync_single_lead(_fb_lead("fb-lead-3", "Meta Three", "+919812346003"))

		# Same form + mapped field values -> validate_duplicate_lead raises
		# DuplicateLeadError, caught internally and written to Failed Lead
		# Sync Log instead of propagating.
		result = source.sync_single_lead(_fb_lead("fb-lead-3", "Meta Three", "+919812346003"))
		self.assertIsNone(result)

		logs = frappe.get_all("Failed Lead Sync Log", filters={"type": "Duplicate"})
		self.assertEqual(len(logs), 1)

	def test_sync_aborts_when_paid_lead_source_missing(self):
		"""sync() must fail fast (one clear log) rather than let every lead in the
		batch fail individually into Failed Lead Sync Log with a LinkValidationError."""
		source = FacebookSyncSource("token", "form-fb-4")
		with (
			patch("frappe.db.exists", return_value=False),
			patch("frappe.log_error") as mock_log,
			patch.object(FacebookSyncSource, "fetch_leads") as mock_fetch,
		):
			source.sync()
		mock_fetch.assert_not_called()
		self.assertTrue(mock_log.called)


class TestLeadSyncSourceSubSourceScope(FrappeTestCase):
	"""B4 — sub_source must be scoped to source='Paid', enforced server-side
	(the Vue Link's :filters="{source:'Paid'}" is cosmetic/client-side only)."""

	def setUp(self):
		super().setUp()
		self._fb_fetch_patch = patch(
			"crm.lead_syncing.doctype.lead_sync_source.lead_sync_source.fetch_and_store_pages_from_facebook"
		)
		self._fb_fetch_patch.start()

	def tearDown(self):
		self._fb_fetch_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_mismatched_sub_source_scope_raises(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			frappe.get_doc(
				{
					"doctype": "Lead Sync Source",
					"name": "Test Wrong Scope Source",
					"type": "Facebook",
					"access_token": "dummy-token",
					"sub_source": "Website",  # scoped to source=Direct, not Paid
				}
			).insert(ignore_permissions=True)
		self.assertIn("not scoped to source", str(ctx.exception))

	def test_correctly_scoped_sub_source_saves(self):
		doc = frappe.get_doc(
			{
				"doctype": "Lead Sync Source",
				"name": "Test Correct Scope Source",
				"type": "Facebook",
				"access_token": "dummy-token",
				"sub_source": "Meta Generic",  # scoped to source=Paid
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.sub_source, "Meta Generic")


class TestLeadSyncSource(FrappeTestCase):
	"""
	Integration tests for LeadSyncSource.
	Use this class for testing interactions between multiple components.
	"""

	pass

# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for Failed Meta Sync Log's retry_sync().

The important case: retry_sync() must not mark a log "Retried" when sync is
actually disabled/unconfigured — add_users_to_audience/remove_users_from_audience
now raise MetaAudienceNotConfigured in that case instead of silently
returning {}, so a retry attempted while settings are disabled correctly
fails loudly (and leaves status as "Failure") rather than lying that it sent
something it didn't.
"""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.audience import MetaAudienceNotConfigured


class TestRetrySync(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _make_log(self, sync_type: str = "Add") -> frappe.model.document.Document:
		# entity_name is a Dynamic Link (linked via `entity`), so it must
		# point to a real CRM Lead rather than a made-up name.
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "RetryTarget"}).insert(
			ignore_permissions=True
		)
		return frappe.get_doc(
			{
				"doctype": "Failed Meta Sync Log",
				"sync_type": sync_type,
				"entity": "CRM Lead",
				"entity_name": lead.name,
				"payload_row": frappe.as_json(["a", "b", "c", "d", "e"]),
				"status": "Failure",
				"traceback": "original failure traceback",
			}
		).insert(ignore_permissions=True)

	@patch(
		"crm.fcrm.doctype.failed_meta_sync_log.failed_meta_sync_log.add_users_to_audience",
		side_effect=MetaAudienceNotConfigured("disabled"),
	)
	def test_does_not_mark_retried_when_sync_unconfigured(self, _mock_add):
		log = self._make_log("Add")
		with self.assertRaises(MetaAudienceNotConfigured):
			log.retry_sync()
		log.reload()
		self.assertEqual(log.status, "Failure")

	@patch("crm.fcrm.doctype.failed_meta_sync_log.failed_meta_sync_log.add_users_to_audience")
	def test_marks_retried_on_successful_resend(self, mock_add):
		log = self._make_log("Add")
		log.retry_sync()
		mock_add.assert_called_once_with([["a", "b", "c", "d", "e"]])
		log.reload()
		self.assertEqual(log.status, "Retried")

	@patch("crm.fcrm.doctype.failed_meta_sync_log.failed_meta_sync_log.remove_users_from_audience")
	def test_remove_sync_type_calls_remove_not_add(self, mock_remove):
		log = self._make_log("Remove")
		log.retry_sync()
		mock_remove.assert_called_once_with([["a", "b", "c", "d", "e"]])
		log.reload()
		self.assertEqual(log.status, "Retried")

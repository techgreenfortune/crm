"""Brevo C1 nurture un-enrollment when a lead closes (C4 Won / C6 Lost).

Module under test: ``crm/integrations/brevo/api.py::unenroll_from_sequence`` and its
enqueue wiring in ``CRMLead._run_stage_side_effects``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.integrations.brevo import api as brevo_api
from crm.tests.factories import make_lead

UNENROLL = "crm.integrations.brevo.api.unenroll_from_sequence"


def _response(status: int = 204, text: str = "") -> MagicMock:
	m = MagicMock()
	m.ok = 200 <= status < 300
	m.status_code = status
	m.text = text
	return m


class TestUnenrollHandler(FrappeTestCase):
	def setUp(self):
		super().setUp()
		settings = MagicMock(enabled=True, nurture_list_id="23")
		settings.get_password = MagicMock(return_value="K-api-key")
		self._settings_patch = patch("crm.integrations.brevo.api.get_brevo_settings", return_value=settings)
		self._settings_patch.start()
		self.post = MagicMock(return_value=_response())
		self._post_patch = patch("crm.integrations.brevo.api.requests.post", self.post)
		self._post_patch.start()

	def tearDown(self):
		self._post_patch.stop()
		self._settings_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_removes_email_from_nurture_list(self):
		lead = make_lead(phone="+919870001001", email="won@example.com", status="C4")

		brevo_api.unenroll_from_sequence(email="won@example.com", lead_name=lead.name)

		self.post.assert_called_once()
		url = self.post.call_args.args[0]
		self.assertEqual(url, brevo_api.BREVO_REMOVE_FROM_LIST_URL.format(list_id="23"))
		self.assertEqual(self.post.call_args.kwargs["json"], {"emails": ["won@example.com"]})

	def test_skipped_while_another_live_lead_shares_email(self):
		closed = make_lead(
			phone="+919870001002", email="repeat@example.com", status="C6", lost_reason="Chose Competitor"
		)
		make_lead(phone="+919870001003", email="repeat@example.com", status="C1")

		brevo_api.unenroll_from_sequence(email="repeat@example.com", lead_name=closed.name)

		self.post.assert_not_called()

	def test_already_removed_is_not_logged_as_failure(self):
		lead = make_lead(
			phone="+919870001004", email="gone@example.com", status="C6", lost_reason="Chose Competitor"
		)
		self.post.return_value = _response(400, "Contact already removed from list and/or does not exist")

		with patch("frappe.log_error") as log_error:
			brevo_api.unenroll_from_sequence(email="gone@example.com", lead_name=lead.name)
		log_error.assert_not_called()

	def test_other_errors_are_logged(self):
		lead = make_lead(
			phone="+919870001005", email="err@example.com", status="C6", lost_reason="Chose Competitor"
		)
		self.post.return_value = _response(400, "List id does not exist")

		with patch("frappe.log_error") as log_error:
			brevo_api.unenroll_from_sequence(email="err@example.com", lead_name=lead.name)
		log_error.assert_called_once()


class TestUnenrollWiring(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def _close(self, lead, status="C6"):
		lead.reload()
		lead.status = status
		lead.lost_reason = "Chose Competitor"
		with patch("frappe.enqueue") as enqueue:
			lead.save()
		return [c.args[0] if c.args else c.kwargs.get("method") for c in enqueue.call_args_list]

	def test_closing_a_lead_that_was_in_c1_enqueues_unenroll(self):
		lead = make_lead(phone="+919870001010", email="c1@example.com")
		lead.status = "C1"
		with patch("frappe.enqueue"):
			lead.save()

		self.assertIn(UNENROLL, self._close(lead))

	def test_closing_a_lead_never_in_c1_does_not_enqueue(self):
		lead = make_lead(phone="+919870001011", email="c0@example.com")

		self.assertNotIn(UNENROLL, self._close(lead))

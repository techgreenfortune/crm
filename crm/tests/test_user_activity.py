import json

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_call_log, make_lead, make_user
from crm.utils.user_activity import FIELD, backfill, touch

HUMAN = "user-activity-sh@test.local"


class TestLastUserActivity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(email=HUMAN, role_profile="Sales Head", full_name="Activity Head")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.delete_doc("User", HUMAN, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _stamp(self, lead):
		return frappe.db.get_value("CRM Lead", lead, FIELD)

	def _comment(self, lead, content):
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead,
				"content": content,
			}
		).insert(ignore_permissions=True)

	def _last_version_fields(self, lead):
		data = frappe.get_last_doc("Version", filters={"ref_doctype": "CRM Lead", "docname": lead}).data
		return [c[0] for c in json.loads(data).get("changed", [])]

	def test_data_corrections_and_reassignment_do_not_stamp(self):
		lead = make_lead(phone="+919800000981")
		frappe.set_user(HUMAN)
		lead.reload()
		lead.custom_city = "Mumbai"
		lead.save(ignore_permissions=True)
		self.assertIsNone(self._stamp(lead.name))

		frappe.set_user("Administrator")
		lead.reload()
		lead.lead_owner = HUMAN
		lead.save(ignore_permissions=True)
		self.assertIsNone(self._stamp(lead.name))

	def test_stage_change_stamps_without_polluting_version(self):
		lead = make_lead(phone="+919800000986")
		lead.reload()
		lead.status = "C1"
		lead.save(ignore_permissions=True, ignore_version=False)
		self.assertIsNotNone(self._stamp(lead.name))
		self.assertIn("status", self._last_version_fields(lead.name))
		self.assertNotIn(FIELD, self._last_version_fields(lead.name))

	def test_whole_doc_save_does_not_write_back_stale_stamp(self):
		lead = make_lead(phone="+919800000987")
		lead.reload()
		touch(lead.name, "2026-03-10 10:00:00")
		lead.set(FIELD, "2026-01-01 10:00:00")
		lead.custom_city = "Pune"
		lead.save(ignore_permissions=True, ignore_version=False)
		self.assertEqual(str(self._stamp(lead.name)), "2026-03-10 10:00:00")
		self.assertNotIn(FIELD, self._last_version_fields(lead.name))

	def test_comments_skip_automation_except_quote_events(self):
		lead = make_lead(phone="+919800000982").name
		frappe.set_user(HUMAN)

		self._comment(lead, "<p>[AUTOMATION] Lead moved to Cold-Unresponsive (C-stage preserved at C2).")
		self.assertIsNone(self._stamp(lead))

		self._comment(lead, "[AUTOMATION] Quote Request manually initiated by someone.")
		self.assertIsNotNone(self._stamp(lead))

		lead2 = make_lead(phone="+919800000983").name
		self._comment(lead2, "<p>Customer asked for a site visit</p>")
		self.assertIsNotNone(self._stamp(lead2))

	def test_call_log_stamps_at_start_time(self):
		lead = make_lead(phone="+919800000984").name
		make_call_log(lead_name=lead, start_time="2026-03-10 10:00:00")
		self.assertEqual(str(self._stamp(lead)), "2026-03-10 10:00:00")

	def test_touch_never_moves_backward(self):
		lead = make_lead(phone="+919800000985").name
		touch(lead, "2026-03-10 10:00:00")
		touch(lead, "2026-03-01 10:00:00")
		self.assertEqual(str(self._stamp(lead)), "2026-03-10 10:00:00")

	def test_backfill_uses_calls_linked_through_links_table(self):
		lead = make_lead(phone="+919800000988").name
		make_call_log(
			lead_name=lead,
			reference_doctype=None,
			reference_docname=None,
			links=[{"link_doctype": "CRM Lead", "link_name": lead}],
			start_time="2026-04-01 09:00:00",
		)
		self._comment(lead, "<p>[AUTOMATION] Brevo enrollment</p>")
		frappe.db.set_value("CRM Lead", lead, FIELD, None, update_modified=False)

		self.assertEqual(backfill(lead), 1)
		self.assertEqual(str(self._stamp(lead)), "2026-04-01 09:00:00")

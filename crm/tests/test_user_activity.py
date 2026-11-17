import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_call_log, make_lead, make_user
from crm.utils.user_activity import FIELD, touch

HUMAN = "user-activity-sh@test.local"


class TestLastUserActivity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(email=HUMAN, role_profile="Sales Head", full_name="Activity Head")
		frappe.db.commit()

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

	def test_lead_edit_by_human_stamps_but_admin_does_not(self):
		lead = make_lead(phone="+919800000981")
		self.assertIsNone(self._stamp(lead.name))

		lead.reload()
		lead.custom_city = "Pune"
		lead.save(ignore_permissions=True)
		self.assertIsNone(self._stamp(lead.name))

		frappe.set_user(HUMAN)
		lead.reload()
		lead.custom_city = "Mumbai"
		lead.save(ignore_permissions=True)
		self.assertIsNotNone(self._stamp(lead.name))

	def test_comments_skip_automation_except_quote_events(self):
		lead = make_lead(phone="+919800000982").name
		frappe.set_user(HUMAN)

		self._comment(lead, "[AUTOMATION] Lead moved to Cold-Unresponsive (C-stage preserved at C2).")
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

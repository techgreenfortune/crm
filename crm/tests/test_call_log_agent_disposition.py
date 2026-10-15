from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_call_log, make_lead

NO_ANSWER = "No Answer / Not Reachable"


def retry_enqueued(enqueue):
	return any(c.args and c.args[0] == "crm.api.call_log.register_no_answer" for c in enqueue.call_args_list)


class TestAgentOwnedDisposition(FrappeTestCase):
	def setUp(self):
		self.lead = make_lead(phone="+919800000171")

	def test_agent_disposition_is_kept_when_exotel_says_not_answered(self):
		log = make_call_log(lead_name=self.lead.name, status="Call Not Answered", call_type="Outgoing")

		log.disposition = "Casual Inquiry"
		log.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Call Log", log.name, "disposition"), "Casual Inquiry")

	def test_exotel_no_answer_on_outgoing_call_does_not_start_retry(self):
		log = make_call_log(lead_name=self.lead.name, status="Ringing", call_type="Outgoing")

		with patch("frappe.enqueue") as enqueue:
			log.status = "Call Not Answered"
			log.save(ignore_permissions=True)

		self.assertFalse(retry_enqueued(enqueue))

	def test_agent_no_answer_starts_retry(self):
		log = make_call_log(lead_name=self.lead.name, status="Completed", call_type="Outgoing")

		with patch("frappe.enqueue") as enqueue:
			log.disposition = NO_ANSWER
			log.save(ignore_permissions=True)

		self.assertTrue(retry_enqueued(enqueue))

	def test_missed_incoming_call_still_starts_retry(self):
		log = make_call_log(lead_name=self.lead.name, status="Ringing", call_type="Incoming")

		with patch("frappe.enqueue") as enqueue:
			log.status = "Call Not Answered"
			log.save(ignore_permissions=True)

		self.assertTrue(retry_enqueued(enqueue))

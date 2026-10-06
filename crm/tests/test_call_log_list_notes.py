import frappe
from frappe.tests.utils import FrappeTestCase

from crm.fcrm.doctype.crm_call_log.crm_call_log import CRMCallLog


def make_call_log(call_id, note=None):
	log = frappe.get_doc(
		{
			"doctype": "CRM Call Log",
			"id": call_id,
			"type": "Outgoing",
			"status": "Completed",
			"from": "9000000001",
			"to": "9000000002",
			"telephony_medium": "Exotel",
		}
	)
	if note:
		log.append("links", {"link_doctype": "FCRM Note", "link_name": note})
	log.insert(ignore_permissions=True, ignore_mandatory=True)
	return log


class TestCallLogListNotes(FrappeTestCase):
	def test_list_rows_flag_call_logs_that_have_a_note(self):
		note = frappe.get_doc({"doctype": "FCRM Note", "title": "Call Note", "content": "x"}).insert(
			ignore_permissions=True
		)
		with_note = make_call_log("list-note-sid-1", note=note.name)
		without_note = make_call_log("list-note-sid-2")

		row = {"type": "Outgoing", "duration": 10, "from": "9000000001", "to": "9000000002"}
		rows = CRMCallLog.parse_list_data(
			[frappe._dict(row, name=with_note.name), frappe._dict(row, name=without_note.name)]
		)

		self.assertEqual([row["_has_note"] for row in rows], [True, False])

	def test_empty_list(self):
		self.assertEqual(CRMCallLog.parse_list_data([]), [])

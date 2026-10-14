import frappe
from frappe.tests.utils import FrappeTestCase

from crm.fcrm.doctype.crm_call_log.crm_call_log import CRMCallLog


def make_call_log(call_id, call_type, from_number, to_number):
	return frappe.get_doc(
		{
			"doctype": "CRM Call Log",
			"id": call_id,
			"type": call_type,
			"status": "Completed",
			"from": from_number,
			"to": to_number,
			"telephony_medium": "Exotel",
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)


class TestCallLogListCustomerNumber(FrappeTestCase):
	def test_customer_number_is_the_other_party(self):
		incoming = make_call_log("list-cust-sid-1", "Incoming", "9000000011", "9000000099")
		outgoing = make_call_log("list-cust-sid-2", "Outgoing", "9000000099", "9000000022")

		rows = CRMCallLog.parse_list_data(
			[
				frappe._dict(name=incoming.name, type="Incoming", duration=0, **{"from": "9000000011", "to": "9000000099"}),
				frappe._dict(name=outgoing.name, type="Outgoing", duration=0, **{"from": "9000000099", "to": "9000000022"}),
			]
		)

		self.assertEqual([row["_customer_number"] for row in rows], ["9000000011", "9000000022"])

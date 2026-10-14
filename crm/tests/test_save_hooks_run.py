import frappe
from frappe.tests.utils import FrappeTestCase

from crm.tests.factories import make_call_log, make_lead


# Frappe runs on_update after a save; a controller method named after_save is never called.
class TestSaveHooksRun(FrappeTestCase):
	def test_new_c0_lead_gets_a_call_task(self):
		lead = make_lead(phone="+919800000191")

		self.assertTrue(
			frappe.db.exists(
				"CRM Task",
				{"task_type": "call_lead", "reference_doctype": "CRM Lead", "reference_docname": lead.name},
			)
		)

	def test_disposition_moves_the_lead_stage(self):
		lead = make_lead(phone="+919800000192")
		log = make_call_log(lead_name=lead.name, status="Completed", call_type="Outgoing")

		log.disposition = "Future Requirement"
		log.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "status"), "C1")

	def test_claiming_an_upload_quote_task_leaves_a_trail(self):
		lead = make_lead(phone="+919800000193")
		task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Upload quote",
				"task_type": "upload_quote",
				"status": "Todo",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		).insert(ignore_permissions=True)

		task.assigned_to = "Administrator"
		task.save(ignore_permissions=True)

		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_name": lead.name,
					"content": ["like", "%Upload Quote task claimed by Administrator%"],
				},
			)
		)

	def test_skipped_stage_move_logs_the_validation_error(self):
		lead = make_lead(phone="+919800000194")
		log = make_call_log(lead_name=lead.name, status="Completed", call_type="Outgoing")
		frappe.db.set_value("CRM Lead", lead.name, "custom_fabricator_routing_reason", "Not An Option")
		start = frappe.utils.now_datetime()

		log.disposition = "Route to Fabricator"
		log.save(ignore_permissions=True)

		error = frappe.get_all(
			"Error Log",
			filters={"creation": [">=", start], "method": ["like", "Disposition stage move skipped%"]},
			pluck="error",
		)
		self.assertTrue(error and "Not An Option" in error[0])

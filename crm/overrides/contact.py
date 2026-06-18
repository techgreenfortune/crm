import frappe
from frappe.contacts.doctype.contact.contact import Contact


def prevent_user_contact_sync(doc, method):
	# Frappe auto-creates a Contact for every User via background job (enqueue_after_commit).
	# Background jobs have no active HTTP request — use that to detect and block the sync.
	if doc.user and not getattr(frappe.local, "request", None):
		frappe.throw(frappe._("Auto-Contact creation from User sync is disabled in CRM."))


class CustomContact(Contact):
	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Name",
				"type": "Data",
				"key": "full_name",
				"width": "17rem",
			},
			{
				"label": "Email",
				"type": "Data",
				"key": "email_id",
				"width": "12rem",
			},
			{
				"label": "Phone",
				"type": "Data",
				"key": "mobile_no",
				"width": "12rem",
			},
			{
				"label": "Organization",
				"type": "Data",
				"key": "company_name",
				"width": "12rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "8rem",
			},
		]
		rows = [
			"name",
			"full_name",
			"company_name",
			"email_id",
			"mobile_no",
			"modified",
			"image",
		]
		return {"columns": columns, "rows": rows}

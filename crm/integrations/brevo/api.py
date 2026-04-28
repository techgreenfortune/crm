import frappe
from frappe import _
from crm.integrations.brevo.brevo_handler import get_brevo_settings, send_email


@frappe.whitelist()
def is_brevo_enabled():
	from crm.integrations.brevo.brevo_handler import is_brevo_enabled as _is_enabled
	return _is_enabled()


@frappe.whitelist()
def send_test_email(to_email: str):
	frappe.only_for(["System Manager", "Sales Manager"])
	settings = get_brevo_settings()
	if not settings.enabled:
		frappe.throw(_("Brevo integration is not enabled."))
	send_email(
		recipients=to_email,
		subject="Brevo Test Email — Frappe CRM",
		html_content="<p>This is a test email from your Frappe CRM Brevo integration. It's working!</p>",
	)
	return {"message": f"Test email sent to {to_email}"}

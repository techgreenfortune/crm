import frappe
import requests

from crm.integrations.brevo.brevo_handler import get_brevo_settings, send_email
from crm.integrations.brevo.brevo_handler import is_brevo_enabled as _is_enabled

BREVO_CONTACTS_API_URL = "https://api.brevo.com/v3/contacts"
BREVO_ADD_TO_LIST_URL = "https://api.brevo.com/v3/contacts/lists/{list_id}/contacts/add"


@frappe.whitelist()
def is_brevo_enabled():
	return _is_enabled()


def enroll_in_sequence(email: str, lead_name: str) -> None:
	"""
	Upsert the contact in Brevo and add them to the configured C1 nurture list.
	The list ID is stored in CRM Brevo Settings as nurture_list_id.
	Silently skips if Brevo is disabled or email is missing.
	"""
	if not email:
		return

	settings = get_brevo_settings()
	if not settings.enabled:
		return

	api_key = settings.get_password("api_key")
	nurture_list_id = getattr(settings, "nurture_list_id", None)
	if not nurture_list_id:
		frappe.log_error(
			"nurture_list_id not configured in CRM Brevo Settings",
			"Brevo C1 Nurture: Missing List ID",
		)
		return

	headers = {
		"accept": "application/json",
		"api-key": api_key,
		"content-type": "application/json",
	}

	# Upsert contact
	upsert_response = requests.post(
		BREVO_CONTACTS_API_URL,
		json={"email": email, "updateEnabled": True},
		headers=headers,
		timeout=10,
	)
	if not upsert_response.ok:
		frappe.log_error(
			f"Lead {lead_name}: status {upsert_response.status_code} — {upsert_response.text}",
			"Brevo C1 Nurture Upsert Failed",
		)
		return

	# Add to nurture list
	response = requests.post(
		BREVO_ADD_TO_LIST_URL.format(list_id=nurture_list_id),
		json={"emails": [email]},
		headers=headers,
		timeout=10,
	)

	if not response.ok:
		frappe.log_error(
			f"Lead {lead_name}: status {response.status_code} — {response.text}",
			"Brevo C1 Nurture Enroll Failed",
		)


@frappe.whitelist()
def send_test_email(to_email: str):
	frappe.only_for(["System Manager", "Sales Manager"])
	settings = get_brevo_settings()
	if not settings.enabled:
		frappe.throw(frappe._("Brevo integration is not enabled."))
	send_email(
		recipients=to_email,
		subject="Brevo Test Email — Frappe CRM",
		html_content="<p>This is a test email from your Frappe CRM Brevo integration. It's working!</p>",
	)
	return {"message": f"Test email sent to {to_email}"}

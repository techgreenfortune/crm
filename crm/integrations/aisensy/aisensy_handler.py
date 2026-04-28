import frappe
import requests

AISENSY_API_URL = "https://backend.aisensy.com/campaign/t1/api/v2"


def is_aisensy_enabled() -> bool:
	return bool(frappe.db.get_single_value("CRM AISensy Settings", "enabled"))


def get_aisensy_settings():
	return frappe.get_single("CRM AISensy Settings")


def send_template_message(
	to: str,
	template_name: str,
	variables: list,
	reference_doctype: str = "",
	reference_name: str = "",
) -> dict:
	settings = get_aisensy_settings()
	api_key = settings.get_password("api_key")
	project_id = settings.project_id

	phone = "".join(filter(str.isdigit, to))
	if not phone.startswith("91") and len(phone) == 10:
		phone = "91" + phone

	payload = {
		"apiKey": api_key,
		"campaignName": template_name,
		"destination": phone,
		"userName": project_id,
		"templateParams": variables,
		"source": "frappe-crm",
		"media": {},
		"buttons": [],
		"carouselCards": [],
		"location": {},
	}

	response = requests.post(
		AISENSY_API_URL,
		json=payload,
		headers={"Content-Type": "application/json"},
		timeout=10,
	)

	if not response.ok:
		frappe.log_error(
			title="AISensy Send Error",
			message=f"Status {response.status_code}: {response.text}",
		)
		frappe.throw(frappe._("AISensy: failed to send message - {0}").format(response.text))

	result = response.json()

	frappe.get_doc(
		{
			"doctype": "CRM AISensy Message",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"to": phone,
			"template_name": template_name,
			"variables": str(variables),
			"status": "Sent",
			"message_id": result.get("messageId", ""),
		}
	).insert(ignore_permissions=True)

	return result

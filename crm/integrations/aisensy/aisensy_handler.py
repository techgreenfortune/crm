import frappe
import requests

AISENSY_API_URL = "https://backend.aisensy.com/campaign/t1/api/v2"


def is_aisensy_enabled() -> bool:
	return bool(frappe.db.get_single_value("CRM AISensy Settings", "enabled"))


def get_aisensy_settings():
	return frappe.get_single("CRM AISensy Settings")


@frappe.whitelist()
def send_template_message(
	to: str,
	template_name: str,
	variables: list,
	reference_doctype: str = "",
	reference_name: str = "",
	recipient_name: str | None = None,
	media_url: str | None = None,
	media_filename: str | None = None,
	source: str | None = None,
	buttons: list | None = None,
	attributes: dict | None = None,
	params_fallback_value: dict | None = None,
) -> dict:
	settings = get_aisensy_settings()
	api_key = settings.get_password("api_key")
	user_name = settings.default_user_name or ""

	phone = "".join(c for c in to if c.isdigit())
	if phone.startswith("91") and len(phone) == 12:
		phone = phone[2:]

	# AiSensy expects media as {"url": "...", "filename": "..."}. Empty dict
	# means no media attachment. Filename defaults to the basename of the URL.
	media: dict = {}
	if media_url:
		media["url"] = media_url
		media["filename"] = media_filename or media_url.rsplit("/", 1)[-1] or "attachment"

	# Mirrors the structure AiSensy's campaign API v2 expects when the template
	# has dynamic button params, fallback values for template variables, or
	# custom attributes. Empty defaults are sent as-is — AiSensy ignores empty
	# arrays/dicts but rejects missing keys for some campaign configs.
	payload = {
		"apiKey": api_key,
		"campaignName": template_name,
		"destination": phone,
		"userName": user_name,
		"templateParams": variables,
		"source": source or "frappe-crm",
		"media": media,
		"buttons": buttons or [],
		"carouselCards": [],
		"location": {},
		"attributes": attributes or {},
		"paramsFallbackValue": params_fallback_value or {},
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
			# AiSensy v2 campaign API returns `submitted_message_id` in the success body
			# (verified empirically 2026-05-12 against backend.aisensy.com/campaign/t1/api/v2:
			# `{"success": "true", "submitted_message_id": "<uuid>"}`). Earlier code read
			# `messageId` which is not a real key — every row got message_id = "".
			"message_id": result.get("submitted_message_id", ""),
		}
	).insert(ignore_permissions=True)

	return result

import time

import frappe
import requests
from frappe import _

from crm.integrations.brevo.brevo_handler import get_brevo_settings, send_email
from crm.integrations.brevo.brevo_handler import is_brevo_enabled as _is_enabled

BREVO_CONTACTS_API_URL = "https://api.brevo.com/v3/contacts"
BREVO_ADD_TO_LIST_URL = "https://api.brevo.com/v3/contacts/lists/{list_id}/contacts/add"

# Cap on the Retry-After header so a malicious or misconfigured value can't
# stall a worker. Five seconds is well above Brevo's typical rate-limit
# windows but short enough that a worker isn't blocked indefinitely.
_RETRY_AFTER_CAP_SECONDS = 5.0


def _post_with_retry(url: str, json: dict, headers: dict, timeout: float = 10):
	"""POST with a single retry on HTTP 429 (Brevo rate limit).

	Returns the final ``requests.Response``. The caller's existing 4xx/5xx
	handling (log_error + early return) works unchanged — this helper only
	adds one retry on a rate-limit response.
	"""
	response = requests.post(url, json=json, headers=headers, timeout=timeout)
	if response.status_code != 429:
		return response

	retry_after = response.headers.get("Retry-After")
	try:
		delay = float(retry_after) if retry_after else 1.0
	except (TypeError, ValueError):
		delay = 1.0
	time.sleep(min(delay, _RETRY_AFTER_CAP_SECONDS))
	return requests.post(url, json=json, headers=headers, timeout=timeout)


@frappe.whitelist()
def is_brevo_enabled():
	return _is_enabled()


@frappe.whitelist()
def enroll_in_sequence(email: str, lead_name: str) -> None:
	"""
	Upsert the contact in Brevo and add them to the configured C1 nurture list.
	The list ID is stored in CRM Brevo Settings as nurture_list_id.
	Silently skips if Brevo is disabled or email is missing.

	Decorated with @frappe.whitelist() because the Lead After-Save server script
	enqueues this function via frappe.enqueue(...); Frappe's RestrictedPython
	gate on frappe.enqueue targets requires the destination to be whitelisted.

	**Anti-spam guard:** `email` must match the lead's `email` field on record.
	This prevents an authenticated user from POSTing to the endpoint with an
	arbitrary email + lead_name pair to inject spam contacts into the Brevo
	nurture list. The server-script enqueue path passes `doc.email` straight
	from the lead, so it satisfies the match trivially. Caller must also have
	`read` permission on the lead — `frappe.get_doc` enforces this implicitly.
	"""
	if not email:
		return

	# Permission + email-match gate. frappe.get_doc throws PermissionError if
	# the caller can't read the lead.
	lead = frappe.get_doc("CRM Lead", lead_name)
	if lead.email != email:
		frappe.throw(
			_("Email must match the lead's stored email."),
			frappe.PermissionError,
		)

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

	# Upsert contact (retries once on 429)
	upsert_response = _post_with_retry(
		BREVO_CONTACTS_API_URL,
		json={"email": email, "updateEnabled": True},
		headers=headers,
	)
	if not upsert_response.ok:
		frappe.log_error(
			f"Lead {lead_name}: status {upsert_response.status_code} — {upsert_response.text}",
			"Brevo C1 Nurture Upsert Failed",
		)
		return

	# Add to nurture list (retries once on 429)
	response = _post_with_retry(
		BREVO_ADD_TO_LIST_URL.format(list_id=nurture_list_id),
		json={"emails": [email]},
		headers=headers,
	)

	if not response.ok:
		frappe.log_error(
			f"Lead {lead_name}: status {response.status_code} — {response.text}",
			"Brevo C1 Nurture Enroll Failed",
		)
		# Contact was upserted to Brevo but list-add failed — surface the gap
		# on the lead so a manual re-enrollment isn't mistaken for a first
		# attempt. try/except so an audit-write failure never breaks the worker.
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": (
						f"[AUTOMATION] Brevo enrollment incomplete — contact upserted "
						f"but list-add failed (HTTP {response.status_code}). "
						f"Manual re-enrollment may be required."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass


@frappe.whitelist()
def send_test_email(to_email: str):
	frappe.only_for(["System Manager", "Sales Head", "Sales Coordinator"])
	settings = get_brevo_settings()
	if not settings.enabled:
		frappe.throw(frappe._("Brevo integration is not enabled."))
	send_email(
		recipients=to_email,
		subject="Brevo Test Email — Frappe CRM",
		html_content="<p>This is a test email from your Frappe CRM Brevo integration. It's working!</p>",
	)
	return {"message": f"Test email sent to {to_email}"}

import frappe
import requests

from crm.fcrm.doctype.crm_brevo_settings.crm_brevo_settings import CRMBrevoSettings

BREVO_SMTP_API_URL = "https://api.brevo.com/v3/smtp/email"


def is_brevo_enabled():
	return bool(frappe.db.get_single_value("CRM Brevo Settings", "enabled"))


def get_brevo_settings() -> CRMBrevoSettings:
	return frappe.get_single("CRM Brevo Settings")  # type: ignore[return-value]


def send_email(recipients, subject, html_content, sender_email=None, sender_name=None):
	settings = get_brevo_settings()
	api_key = settings.get_password("api_key")

	_sender_email = sender_email or settings.sender_email
	_sender_name = sender_name or settings.sender_name or "Frappe CRM"

	if isinstance(recipients, str):
		recipients = [recipients]

	payload = {
		"sender": {"name": _sender_name, "email": _sender_email},
		"to": [{"email": r} for r in recipients],
		"subject": subject,
		"htmlContent": html_content,
	}

	response = requests.post(
		BREVO_SMTP_API_URL,
		json=payload,
		headers={
			"accept": "application/json",
			"api-key": api_key,
			"content-type": "application/json",
		},
		timeout=10,
	)

	if not response.ok:
		frappe.log_error(
			title="Brevo Email Error",
			message=f"Status {response.status_code}: {response.text}",
		)
		frappe.throw(f"Brevo: failed to send email — {response.text}")

	return response.json()


def send_template_email(
	template_id,
	recipients,
	params,
	sender_email=None,
	sender_name=None,
	attachments=None,
	cc=None,
):
	"""Send a Brevo transactional email rendered from a template ID + params.

	The body lives in Brevo (designed in their dashboard) — Python only supplies
	the template ID and the variable bag the template references via {{ params.X }}.

	``attachments``: optional list of dicts with shape
	``[{"name": "file.pdf", "content": "<base64-string>"}, ...]``.  Brevo
	supports up to ~10MB total per request.

	``cc``: optional list of CC email addresses.  Strings or None are tolerated.
	"""
	settings = get_brevo_settings()
	api_key = settings.get_password("api_key")

	_sender_email = sender_email or settings.sender_email
	_sender_name = sender_name or settings.sender_name or "Frappe CRM"

	if isinstance(recipients, str):
		recipients = [recipients]
	if isinstance(cc, str):
		cc = [cc]

	payload = {
		"sender": {"name": _sender_name, "email": _sender_email},
		"to": [{"email": r} for r in recipients if r],
		"templateId": int(template_id),
		"params": params or {},
	}

	if cc:
		cc_list = [{"email": c} for c in cc if c]
		if cc_list:
			payload["cc"] = cc_list

	if attachments:
		payload["attachment"] = attachments

	if not payload["to"]:
		frappe.log_error(
			title="Brevo Template Email — no recipients",
			message=f"templateId={template_id} params={params}",
		)
		return None

	response = requests.post(
		BREVO_SMTP_API_URL,
		json=payload,
		headers={
			"accept": "application/json",
			"api-key": api_key,
			"content-type": "application/json",
		},
		timeout=10,
	)

	if not response.ok:
		frappe.log_error(
			title="Brevo Template Email Error",
			message=f"templateId={template_id} status={response.status_code}: {response.text}",
		)
		frappe.throw(f"Brevo: failed to send template email — {response.text}")

	return response.json()


def send_invitation_email(recipient_email, invite_link):
	title = "Frappe CRM"
	html_content = f"""
		<p>You have been invited to join {title}</p>
		<p><a href="{invite_link}">Accept Invitation</a></p>
	"""
	return send_email(
		recipients=recipient_email,
		subject=f"You have been invited to join {title}",
		html_content=html_content,
	)

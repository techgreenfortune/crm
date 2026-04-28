import frappe
import requests


BREVO_SMTP_API_URL = "https://api.brevo.com/v3/smtp/email"


def is_brevo_enabled():
	return bool(frappe.db.get_single_value("CRM Brevo Settings", "enabled"))


def get_brevo_settings():
	settings = frappe.get_single("CRM Brevo Settings")
	return settings


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


def send_invitation_email(recipient_email, invite_link):
	title = "Frappe CRM"
	html_content = frappe.render_template(
		"crm/templates/emails/crm_invitation.html",
		{"title": title, "invite_link": invite_link},
	)
	return send_email(
		recipients=recipient_email,
		subject=f"You have been invited to join {title}",
		html_content=html_content,
	)

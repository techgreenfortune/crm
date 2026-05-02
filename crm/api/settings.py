import frappe
import requests
from frappe import _
from frappe.utils import get_url


@frappe.whitelist()
def get_opsgate_settings():
	return {
		"opsgate_enabled": bool(frappe.db.get_single_value("FCRM Settings", "opsgate_enabled")),
		"opsgate_url": frappe.db.get_single_value("FCRM Settings", "opsgate_url") or "",
	}


@frappe.whitelist()
def get_opsgate_url() -> str:
	return frappe.db.get_single_value("FCRM Settings", "opsgate_url") or ""


@frappe.whitelist()
def get_opsgate_redirect_url():
	opsgate_url = (frappe.db.get_single_value("FCRM Settings", "opsgate_url") or "").rstrip("/")
	if not opsgate_url:
		frappe.throw(_("OpsGate URL is not configured"))

	sso_secret = frappe.conf.get("crm_sso_secret")
	if not sso_secret:
		frappe.throw(_("CRM SSO secret is not configured. Add crm_sso_secret to site_config.json"))

	opsgate_api_url = (frappe.conf.get("opsgate_api_url") or "").rstrip("/")
	if not opsgate_api_url:
		frappe.throw(_("OpsGate API URL is not configured. Add opsgate_api_url to site_config.json"))

	# frappe.session.user is 'Administrator' for admin — fetch the actual email from User doctype
	user_email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
	sso_endpoint = f"{opsgate_api_url}/user/sso-token"

	frappe.logger().info(f"OpsGate SSO: calling {sso_endpoint} for {user_email}")

	try:
		resp = requests.post(
			sso_endpoint,
			json={"email": user_email, "sso_secret": sso_secret},
			timeout=10,
		)
		if not resp.ok:
			frappe.logger().error(f"OpsGate SSO error {resp.status_code}: {resp.text}")
			frappe.throw(f"OpsGate SSO failed ({resp.status_code}): {resp.text}")
		data = resp.json().get("data", {})
	except requests.RequestException as e:
		frappe.logger().error(f"OpsGate SSO request failed: {e}")
		frappe.throw(f"Could not reach OpsGate backend: {e}")

	access_token = data.get("access_token", "")
	refresh_token = data.get("refresh_token", "")
	expires_at = data.get("access_token_expires_at", "")

	if not access_token:
		frappe.throw(_("OpsGate SSO returned no access token — check that the user email exists in OpsGate"))

	redirect_url = (
		f"{opsgate_url}/auth/sso?token={access_token}&refresh={refresh_token}&expires_at={expires_at}"
	)
	return {"redirect_url": redirect_url}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
def get_crm_login_url():
	sso_secret = frappe.conf.get("crm_sso_secret")
	if not sso_secret:
		frappe.throw(_("CRM SSO secret is not configured"), frappe.AuthenticationError)

	# frappe.form_dict is populated from both form-encoded and JSON bodies
	provided_secret = frappe.form_dict.get("sso_secret", "")
	if provided_secret != sso_secret:
		frappe.throw(_("Unauthorized"), frappe.AuthenticationError)

	email = frappe.form_dict.get("email", "")
	if not email:
		frappe.throw(_("email is required"))

	if not frappe.db.exists("User", email):
		frappe.throw(_("User {0} does not exist in CRM").format(email), frappe.DoesNotExistError)

	if not frappe.db.get_value("User", email, "enabled"):
		frappe.throw(_("User {0} is disabled in CRM. Please contact your administrator.").format(email), frappe.ValidationError)

	key = frappe.generate_hash()
	frappe.cache.set_value(f"one_time_login_key:{key}", email, expires_in_sec=120)
	login_url = get_url(f"/api/method/frappe.www.login.login_via_key?key={key}")
	return {"login_url": login_url}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
def create_crm_user():
	sso_secret = frappe.conf.get("crm_sso_secret")
	if not sso_secret:
		frappe.throw(_("CRM SSO secret is not configured"), frappe.AuthenticationError)

	provided_secret = frappe.form_dict.get("sso_secret", "")
	if provided_secret != sso_secret:
		frappe.throw(_("Unauthorized"), frappe.AuthenticationError)

	email = frappe.form_dict.get("email", "")
	if not email:
		frappe.throw(_("email is required"))

	first_name = frappe.form_dict.get("first_name", "") or email.split("@")[0].title()
	last_name = frappe.form_dict.get("last_name", "") or ""
	role = frappe.form_dict.get("role", "Sales User")

	valid_roles = ("Sales User", "Sales Manager", "System Manager")
	if role not in valid_roles:
		role = "Sales User"

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			doctype="User",
			user_type="System User",
			email=email,
			send_welcome_email=0,
			first_name=first_name,
			last_name=last_name,
		).insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)

	user.append_roles(role)
	if role == "System Manager":
		user.append_roles("Sales Manager", "Sales User")
	elif role == "Sales Manager":
		user.append_roles("Sales User")

	if role == "Sales User":
		block_modules = frappe.get_all(
			"Module Def",
			fields=["name as module"],
			filters={"name": ["!=", "FCRM"]},
		)
		if block_modules:
			user.set("block_modules", block_modules)

	user.save(ignore_permissions=True)

	return {"email": email, "created": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
def disable_crm_user():
	sso_secret = frappe.conf.get("crm_sso_secret")
	if not sso_secret:
		frappe.throw(_("CRM SSO secret is not configured"), frappe.AuthenticationError)

	provided_secret = frappe.form_dict.get("sso_secret", "")
	if provided_secret != sso_secret:
		frappe.throw(_("Unauthorized"), frappe.AuthenticationError)

	email = frappe.form_dict.get("email", "")
	if not email:
		frappe.throw(_("email is required"))

	if not frappe.db.exists("User", email):
		return {"email": email, "disabled": False, "reason": "user not found in CRM"}

	frappe.db.set_value("User", email, "enabled", 0)
	return {"email": email, "disabled": True}


@frappe.whitelist()
def create_email_account(data: dict):
	service = data.get("service")
	service_config = email_service_config.get(service)
	if not service_config:
		return "Service not supported"

	try:
		email_doc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_id": data.get("email_id"),
				"email_account_name": data.get("email_account_name"),
				"service": service,
				"enable_incoming": data.get("enable_incoming"),
				"enable_outgoing": data.get("enable_outgoing"),
				"default_incoming": data.get("default_incoming"),
				"default_outgoing": data.get("default_outgoing"),
				"email_sync_option": "ALL",
				"initial_sync_count": 100,
				"create_contact": 1,
				"track_email_status": 1,
				"use_tls": 1,
				"use_imap": 1,
				"smtp_port": 587,
				**service_config,
			}
		)
		if service == "Frappe Mail":
			email_doc.api_key = data.get("api_key")
			email_doc.api_secret = data.get("api_secret")
			email_doc.frappe_mail_site = data.get("frappe_mail_site")
			email_doc.append_to = "CRM Lead"
		else:
			email_doc.append("imap_folder", {"append_to": "CRM Lead", "folder_name": "INBOX"})
			email_doc.password = data.get("password")
			# validate whether the credentials are correct
			email_doc.get_incoming_server()

		# if correct credentials, save the email account
		email_doc.save()
	except Exception as e:
		frappe.throw(str(e))


email_service_config = {
	"Frappe Mail": {
		"domain": None,
		"password": None,
		"awaiting_password": 0,
		"ascii_encode_password": 0,
		"login_id_is_different": 0,
		"login_id": None,
		"use_imap": 0,
		"use_ssl": 0,
		"validate_ssl_certificate": 0,
		"use_starttls": 0,
		"email_server": None,
		"incoming_port": 0,
		"always_use_account_email_id_as_sender": 1,
		"use_tls": 0,
		"use_ssl_for_outgoing": 0,
		"smtp_server": None,
		"smtp_port": None,
		"no_smtp_authentication": 0,
	},
	"GMail": {
		"email_server": "imap.gmail.com",
		"use_ssl": 1,
		"smtp_server": "smtp.gmail.com",
	},
	"Outlook": {
		"email_server": "imap-mail.outlook.com",
		"use_ssl": 1,
		"smtp_server": "smtp-mail.outlook.com",
	},
	"Sendgrid": {
		"smtp_server": "smtp.sendgrid.net",
		"smtp_port": 587,
	},
	"SparkPost": {
		"smtp_server": "smtp.sparkpostmail.com",
	},
	"Yahoo": {
		"email_server": "imap.mail.yahoo.com",
		"use_ssl": 1,
		"smtp_server": "smtp.mail.yahoo.com",
		"smtp_port": 587,
	},
	"Yandex": {
		"email_server": "imap.yandex.com",
		"use_ssl": 1,
		"smtp_server": "smtp.yandex.com",
		"smtp_port": 587,
	},
}

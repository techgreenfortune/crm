import re
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils import add_to_date, get_datetime, get_system_timezone, now_datetime
from frappe.utils.data import cstr

from crm.integrations.api import get_contact_by_phone_number

DEFAULT_SOFTPHONE_API_HOST = "integrationscore.mum1.exotel.com"
SOFTPHONE_TOKEN_CACHE_SECONDS = 60 * 60
# The only usermapping fields the browser needs to register its own SIP device.
SOFTPHONE_BROWSER_MAPPING_FIELDS = (
	"AppID",
	"AppUserId",
	"SipId",
	"SipSecret",
	"ExotelUserName",
	"ExotelAccountSid",
)
SOFTPHONE_DIALS_PER_MINUTE = 10
# Looked up while the call rings; the agent can't accept until registration returns.
REGISTER_LOOKUP_TIMEOUT_SECONDS = 3


class ExotelDialOutcomeUnknown(frappe.ValidationError):
	"""Exotel may have placed the call even though its response never arrived."""


# Endpoints for webhook

# Incoming Call:
# <site>/api/method/crm.integrations.exotel.handler.handle_request?key=<exotel-webhook-verify-token>

# Exotel Reference:
# https://developer.exotel.com/api/
# https://support.exotel.com/support/solutions/articles/48283-working-with-passthru-applet


# Incoming Call
# Security review: guest access is intentional. Exotel sends no request signature, so
# validate_request() only checks a shared `key` query parameter against webhook_verify_token.
# fmt: off
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
# fmt: on
def handle_request(**kwargs):
	validate_request()
	if not is_integration_enabled():
		return

	request_log = create_request_log(
		kwargs,
		request_description="Exotel Call",
		service_name="Exotel",
		request_headers=frappe.request.headers,
		is_remote_request=1,
	)

	try:
		request_log.status = "Completed"
		exotel_settings = get_exotel_settings()
		if not exotel_settings.enabled:
			return

		call_payload = normalize_call_payload(kwargs)
		if call_payload.get("AppUserID"):
			# Guest webhook: an unverified AppUserID must not become the call's agent.
			call_payload.AgentEmail = get_softphone_agent_user(call_payload.AppUserID)
		agent_email = call_payload.get("AgentEmail")

		frappe.logger("exotel").info(
			f"[Exotel] webhook received | EventType={call_payload.get('EventType')} "
			f"Status={call_payload.get('Status')} Direction={call_payload.get('Direction')} "
			f"CallSid={call_payload.get('CallSid')}"
		)

		# "free" = agent released. Softphone inbound notifications send it for answered, missed
		# and rejected calls alike, so the outcome comes from the Calls API instead.
		status = call_payload.get("Status")
		if status == "free":
			if call_payload.get("AppUserID") and frappe.db.exists("CRM Call Log", call_payload.get("CallSid")):
				frappe.enqueue(
					"crm.integrations.exotel.handler.reconcile_call_log",
					call_sid=call_payload.get("CallSid"),
					created_at=now_datetime(),
					enqueue_after_commit=True,
				)
			return

		if call_log := get_call_log(call_payload):
			try:
				call_log = update_call_log(call_payload, call_log=call_log)
				if not call_log:
					return
			except Exception:
				frappe.log_error(title="Error while updating call log")
		else:
			if call_payload.get("AppUserID") and not agent_email:
				frappe.logger("exotel").warning(
					f"[Exotel] skipped call log create for unknown AppUserID | CallSid={call_payload.get('CallSid')}"
				)
				return
			outgoing = (call_payload.get("Direction") or "").startswith("outbound")
			status = get_call_log_status(call_payload, call_payload.get("Direction"))
			call_log = create_call_log(
				call_id=call_payload.get("CallSid"),
				from_number=call_payload.get("CallFrom"),
				to_number=get_call_log_to_number(call_payload),
				medium=call_payload.get("VirtualNumber") or call_payload.get("To"),
				status=status if is_valid_call_log_status(status) else "Ringing",
				agent=agent_email,
				call_type="Outgoing" if outgoing else "Incoming",
			)

		# Publish realtime AFTER DB commit so the call log exists when the
		# frontend acts on the event. Terminal events from Exotel don't reliably
		# include AgentEmail, so fall back to the call log's caller/receiver
		# (set when the call started).
		target_user = agent_email or (call_log and (call_log.caller or call_log.receiver))
		# No agent (e.g. a voicemail when nobody was dialled) means no popup shows this call;
		# user=None would broadcast it into every open popup.
		if not target_user:
			return
		frappe.publish_realtime("exotel_call", call_payload, user=target_user)
		frappe.logger("exotel").info(
			f"[Exotel] publish_realtime fired | CallSid={call_payload.get('CallSid')} "
			f"AgentEmail={agent_email} ResolvedTarget={target_user}"
		)
	except Exception:
		request_log.status = "Failed"
		request_log.error = frappe.get_traceback()
		frappe.db.rollback()
		frappe.log_error(title="Error while creating/updating call record")
		frappe.db.commit()
	finally:
		request_log.save(ignore_permissions=True)
		frappe.db.commit()


# Outgoing Call
@frappe.whitelist()
def make_a_call(
	to_number: str,
	from_number: str | None = None,
	caller_id: str | None = None,
	reference_doctype: str | None = None,
	reference_docname: str | None = None,
):
	if not is_integration_enabled():
		frappe.throw(_("Please setup Exotel intergration"), title=_("Integration Not Enabled"))

	endpoint = get_exotel_endpoint("Calls/connect.json?details=true")

	if not from_number:
		from_number = frappe.get_value("CRM Telephony Agent", {"user": frappe.session.user}, "mobile_no")

	if not caller_id:
		caller_id = frappe.get_value("CRM Telephony Agent", {"user": frappe.session.user}, "exotel_number")

	if not caller_id:
		frappe.throw(
			_("You do not have Exotel Number set in your Telephony Agent"), title=_("Exotel Number Missing")
		)

	if caller_id and caller_id not in get_all_exophones():
		frappe.throw(_("Exotel Number {0} is not valid").format(caller_id), title=_("Invalid Exotel Number"))

	if not from_number:
		frappe.throw(
			_("You do not have mobile number set in your Telephony Agent"), title=_("Mobile Number Missing")
		)

	record_call = frappe.db.get_single_value("CRM Exotel Settings", "record_call")

	try:
		response = requests.post(
			endpoint,
			data={
				"From": from_number,
				"To": to_number,
				"CallerId": caller_id,
				"Record": "true" if record_call else "false",
				"StatusCallback": get_status_updater_url(),
				"StatusCallbackEvents[0]": "terminal",
				"StatusCallbackEvents[1]": "answered",
			},
		)
		response.raise_for_status()
	except requests.exceptions.HTTPError:
		if exc := response.json().get("RestException"):
			frappe.throw(exc.get("Message"), title=_("Exotel Exception"))
		else:
			frappe.throw(_("Exotel call failed — check Error Log for details"), title=_("Exotel Error"))
	else:
		res = response.json()
		call_payload = res.get("Call", {})

		create_call_log(
			call_id=call_payload.get("Sid"),
			from_number=call_payload.get("From"),
			to_number=call_payload.get("To"),
			medium=call_payload.get("PhoneNumberSid"),
			call_type="Outgoing",
			agent=frappe.session.user,
			reference_doctype=reference_doctype,
			reference_docname=reference_docname,
		)

		call_details = res.get("Call", {})
		call_details["CallSid"] = call_details.get("Sid", "")
		return call_details


@frappe.whitelist()
def get_softphone_config():
	settings = get_exotel_settings()
	agent = _get_current_softphone_agent()
	if not settings.enabled or not settings.softphone_enabled or not agent:
		return {"enabled": False}
	_require_softphone_agent_setup(agent)

	mapping = _get_softphone_user_mapping(_get_configured_softphone_token(settings), _get_agent_email(agent))
	if not mapping:
		frappe.throw(
			_("No Exotel softphone user is linked to you. Save your Telephony Agent again."),
			title=_("Softphone Unavailable"),
		)
	if cstr(mapping.get("SipId")).strip() != agent.exotel_sip_id:
		frappe.throw(
			_("Your Exotel SIP device has changed. Save your Telephony Agent again."),
			title=_("Softphone Unavailable"),
		)

	config = {field: mapping.get(field) for field in SOFTPHONE_BROWSER_MAPPING_FIELDS}
	if not all(config.values()):
		frappe.log_error(
			title="Incomplete Exotel softphone user mapping",
			message=f"Missing: {[field for field, value in config.items() if not value]}",
		)
		frappe.throw(_("Exotel returned incomplete softphone details."), title=_("Softphone Unavailable"))
	return {"enabled": True, **config}


@frappe.whitelist(methods=["POST"])
def make_softphone_call(
	phone_number: str,
	reference_doctype: str | None = None,
	reference_docname: str | None = None,
):
	settings = get_exotel_settings()
	agent = _get_current_softphone_agent()
	if not settings.enabled or not settings.softphone_enabled or not agent:
		frappe.throw(_("Exotel browser softphone is not enabled for this user."), frappe.PermissionError)
	_require_softphone_agent_setup(agent)

	phone_number = cstr(phone_number).strip()
	if len(last_ten_digits(phone_number)) < 10:
		frappe.throw(_("Enter a valid phone number to call."))
	_validate_softphone_reference(reference_doctype, reference_docname)
	_check_softphone_dial_rate()

	call_sid = _place_softphone_call(settings, agent, phone_number)
	# The call is live from here on. A failure now must not tell the browser "not placed": it would
	# treat the agent leg as a new inbound call and let the agent dial again. The Integration Core
	# callback creates the missing log when its first event arrives (handle_request's create path);
	# reconcile can't, because it only repairs logs that exist.
	try:
		_create_softphone_call_log(agent, call_sid, phone_number, "Outgoing", reference_doctype, reference_docname)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Exotel softphone call log not created: {call_sid}")
	return {"CallSid": call_sid}


@frappe.whitelist()
def register_softphone_call(
	call_sid: str,
	phone_number: str,
	call_type: str,
	reference_doctype: str | None = None,
	reference_docname: str | None = None,
):
	settings = get_exotel_settings()
	agent = _get_current_softphone_agent()
	if not settings.enabled or not settings.softphone_enabled or not agent:
		frappe.throw(_("Exotel browser softphone is not enabled for this user."), frappe.PermissionError)
	_require_softphone_agent_setup(agent)

	call_sid = cstr(call_sid).strip()
	phone_number = cstr(phone_number).strip()
	if not call_sid or len(call_sid) > 140 or not phone_number:
		frappe.throw(_("Invalid softphone call details."))
	# Outgoing logs are created by make_softphone_call from Exotel's own dial response.
	if call_type != "Incoming":
		frappe.throw(_("Invalid softphone call type."))

	# CallFrom is the caller number the popup should show: the browser's comes from the SIP caller ID,
	# which can be wrong, so the webhook-created log or Exotel's own record wins.
	if existing := frappe.db.exists("CRM Call Log", call_sid):
		_claim_existing_softphone_call(existing)
		return {"CallSid": call_sid, "CallFrom": frappe.db.get_value("CRM Call Log", call_sid, "from") or phone_number}

	_validate_softphone_reference(reference_doctype, reference_docname)
	phone_number = _exotel_inbound_caller(call_sid) or phone_number
	_create_softphone_call_log(agent, call_sid, phone_number, call_type, reference_doctype, reference_docname)
	return {"CallSid": call_sid, "CallFrom": phone_number}


def _require_softphone_agent_setup(agent):
	if not agent.exotel_number:
		frappe.throw(_("Exotel Number is required for browser calling."), title=_("Softphone Unavailable"))
	if not agent.exotel_sip_id:
		frappe.throw(
			_("Save your Telephony Agent again to link your Exotel SIP ID before browser calling."),
			title=_("Softphone Unavailable"),
		)


def _exotel_inbound_caller(call_sid):
	"""Exotel's caller number for a ringing call, or None if Exotel can't say yet.

	The browser's number comes from the SIP caller ID, which can be wrong (a SIP user or a platform
	number) or forged. Ownership of the call is not checked here: Exotel's "To" is only confirmed for
	finished calls, so the reconcile job verifies it.
	"""
	try:
		call = fetch_exotel_call(call_sid, timeout=REGISTER_LOOKUP_TIMEOUT_SECONDS)
	except requests.RequestException:
		return None
	if not call or normalize_direction(call.get("Direction")) != "incoming":
		return None
	return cstr(call.get("From")).strip() or None


def _create_softphone_call_log(agent, call_sid, phone_number, call_type, reference_doctype, reference_docname):
	outgoing = call_type == "Outgoing"
	try:
		create_call_log(
			call_id=call_sid,
			from_number=(agent.mobile_no or frappe.session.user) if outgoing else phone_number,
			to_number=phone_number if outgoing else agent.exotel_number,
			medium=agent.exotel_number,
			agent=frappe.session.user,
			call_type=call_type,
			reference_doctype=reference_doctype,
			reference_docname=reference_docname,
			is_softphone_call=True,
		)
	# MariaDB reports a concurrent insert of the same CallSid as error 1020, which Frappe raises
	# as QueryDeadlockError rather than DuplicateEntryError.
	except (frappe.DuplicateEntryError, frappe.QueryDeadlockError):
		frappe.db.rollback()
		if not frappe.db.exists("CRM Call Log", call_sid):
			raise
		_claim_existing_softphone_call(call_sid)


SOFTPHONE_ISSUE_REPORTS_PER_HOUR = 10
SOFTPHONE_ISSUE_LOG_CHARS = 60_000
_SDK_LOG_SECRETS = re.compile(
	r'((?:response|nonce|cnonce|opaque)="|"(?:secret|password|authorizationPassword|sipSecret)"\s*:\s*")[^"]*'
)
_SDK_LOG_NUMBERS = re.compile(r"\d{7,}")


@frappe.whitelist(methods=["POST"])
def report_softphone_issue(call_sid: str, logs: str):
	"""Keep the browser SDK's recent log lines from when the softphone stopped taking calls.

	The SDK writes its log to the agent's browser only; this is how it reaches an Error Log.
	"""
	agent = _get_current_softphone_agent()
	if not agent:
		frappe.throw(_("Exotel browser softphone is not enabled for this user."), frappe.PermissionError)
	key = frappe.cache.make_key(f"crm:exotel:softphone-issue-reports:{frappe.session.user}")
	reports = frappe.cache.incrby(key, 1)
	if reports == 1:
		frappe.cache.expire(key, 60 * 60)
	if reports > SOFTPHONE_ISSUE_REPORTS_PER_HOUR:
		return
	frappe.log_error(
		title="Exotel softphone stopped taking calls",
		message=f"Agent: {agent.user}\nCallSid: {cstr(call_sid)[:64]}\n\n"
		+ mask_softphone_sdk_log(cstr(logs)[-SOFTPHONE_ISSUE_LOG_CHARS:]),
	)


def mask_softphone_sdk_log(text):
	text = _SDK_LOG_SECRETS.sub(lambda m: m.group(1) + "***", text)
	return _SDK_LOG_NUMBERS.sub(lambda m: m.group(0)[:2] + "***" + m.group(0)[-2:], text)


def _check_softphone_dial_rate():
	# Frappe's rate_limit is keyed by IP, and a whole office dials from one IP.
	key = frappe.cache.make_key(f"crm:exotel:softphone-dials:{frappe.session.user}")
	dials = frappe.cache.incrby(key, 1)
	if dials == 1:
		frappe.cache.expire(key, 60)
	if dials > SOFTPHONE_DIALS_PER_MINUTE:
		frappe.throw(_("Too many calls started in the last minute. Wait a moment."), frappe.RateLimitExceededError)


def _place_softphone_call(settings, agent, phone_number):
	unknown_message = _(
		"Exotel did not confirm the call. It may still ring: check your call log before dialling again."
	)
	try:
		response = requests.post(
			f"{_softphone_api_base()}/call/outbound_call",
			headers={"Authorization": _get_configured_softphone_token(settings)},
			# Same fields the SDK's own MakeCall sends; it never sends a usable customer_id.
			json={
				"app_id": cstr(settings.softphone_app_id).strip(),
				"to": phone_number,
				"user_id": _get_agent_email(agent),
			},
			timeout=10,
		)
	except requests.ConnectTimeout:
		frappe.log_error(title="Exotel softphone dial failed")
		frappe.throw(_("Could not reach Exotel. The call was not placed."))
	# Any other failure, including a connection reset, can happen after Exotel accepted the dial.
	except requests.RequestException:
		frappe.log_error(title="Exotel softphone dial outcome unknown")
		frappe.throw(unknown_message, ExotelDialOutcomeUnknown)

	if response.status_code >= 500:
		frappe.log_error(title="Exotel softphone dial outcome unknown", message=response.text)
		frappe.throw(unknown_message, ExotelDialOutcomeUnknown)
	try:
		payload = response.json()
	except ValueError:
		frappe.log_error(title="Exotel softphone dial outcome unknown", message=response.text)
		frappe.throw(unknown_message, ExotelDialOutcomeUnknown)

	data = payload.get("Data") if isinstance(payload, dict) else None
	call_sid = cstr((data or {}).get("CallSid")).strip() if isinstance(data, dict) else ""
	if not response.ok or payload.get("Status") != "Success" or not call_sid:
		frappe.log_error(title="Exotel softphone dial rejected", message=response.text)
		frappe.throw(
			_("Exotel rejected the call: {0}").format(
				cstr(payload.get("Error") if isinstance(payload, dict) else "") or response.status_code
			)
		)

	# Checked when present (every real reply so far has it). A missing FromNumber isn't treated as
	# unknown: that would reject the agent leg of a call Exotel really placed, and the reconcile job
	# verifies the SIP leg from the Calls API anyway.
	from_sip = cstr(data.get("FromNumber")).strip()
	if from_sip and from_sip != agent.exotel_sip_id:
		frappe.log_error(
			title="Exotel softphone dial rang another SIP device",
			message=f"CallSid {call_sid}: expected {agent.exotel_sip_id}, got {from_sip}",
		)
		# Exotel accepted the dial, so this is not a definite failure: stray rings must be rejected.
		frappe.throw(
			_("Exotel placed the call on a different SIP device. Contact an administrator."),
			ExotelDialOutcomeUnknown,
		)
	return call_sid


def get_softphone_agent_user(app_user_id):
	app_user_id = cstr(app_user_id).strip()
	if not app_user_id:
		return None
	user = frappe.db.get_value("User", {"email": app_user_id}, "name")
	if user and frappe.db.exists("CRM Telephony Agent", {"user": user, "exotel_softphone_enabled": 1}):
		return user
	return None


def _get_current_softphone_agent():
	if frappe.session.user == "Guest":
		return None
	return frappe.db.get_value(
		"CRM Telephony Agent",
		{"user": frappe.session.user, "exotel_softphone_enabled": 1},
		["user", "mobile_no", "exotel_number", "exotel_sip_id"],
		as_dict=True,
	)


def ensure_softphone_user_mapping(agent):
	"""Return the agent's SIP ID from their Integration Core user mapping.

	Only an existing Exotel user with a SIP device is mapped: for an unknown email, POST
	/usermapping silently creates a new, possibly billable, Exotel user. Every mapping, new or
	existing, must point at one of that user's own SIP devices.
	"""
	settings = get_exotel_settings()
	app_id = cstr(settings.softphone_app_id).strip()
	app_secret = settings.get_password("softphone_app_secret", raise_exception=False)
	if not settings.softphone_enabled or not app_id or not app_secret:
		frappe.throw(_("Configure the browser softphone in Exotel Settings before enabling it for an agent."))

	email = _get_agent_email(agent)
	devices = _get_exotel_user_devices(settings, email)
	if devices is None:
		frappe.throw(
			_(
				"No Exotel user exists for {0}. Create them in the Exotel dashboard with a SIP device first; "
				"CRM will not create Exotel users because they may incur charges."
			).format(email),
			title=_("Exotel User Not Provisioned"),
		)
	sip_ids = {cstr(d.get("contact_uri")).strip().lower() for d in devices if d.get("type") == "sip"}
	sip_ids.discard("")
	if not sip_ids:
		frappe.throw(
			_("Exotel user {0} has no SIP device. Add a SIP extension for this user in the Exotel dashboard.").format(
				email
			)
		)

	token = _get_softphone_app_token(app_id, app_secret)
	mapping = _get_softphone_user_mapping(token, email)
	if not mapping:
		_create_softphone_user_mapping(token, settings, agent, email, devices)
		mapping = _get_softphone_user_mapping(token, email)

	sip_id = cstr((mapping or {}).get("SipId")).strip()
	# A mapping rejected here stays in Exotel, so a later save must not trust it either.
	if sip_id.lower() not in sip_ids:
		frappe.log_error(
			title="Exotel softphone mapping used an unexpected SIP device",
			message=f"{email}: mapped to {sip_id or 'nothing'}, user's SIP devices are {sorted(sip_ids)}",
		)
		frappe.throw(
			_(
				"Exotel mapped {0} to a SIP device that is not theirs. Check this user in the Exotel "
				"dashboard before enabling browser calling."
			).format(email)
		)
	return sip_id


def _get_exotel_user_devices(settings, email):
	"""Return the devices of the account's Exotel user with this email, or None if there is none."""
	host = cstr(settings.subdomain).strip().removeprefix("api.")
	try:
		response = requests.get(
			f"https://ccm-api.{host}/v2/accounts/{settings.account_sid}/users",
			params={"fields": "devices", "email": email},
			auth=(settings.api_key, settings.get_password("api_token")),
			timeout=10,
		)
		response.raise_for_status()
		users = response.json().get("response")
	except (requests.RequestException, ValueError, AttributeError):
		frappe.log_error(title="Exotel user lookup failed")
		frappe.throw(_("Could not look up the Exotel user. Try again later."))
	for item in users if isinstance(users, list) else []:
		user = (item or {}).get("data") or {}
		if cstr(user.get("email")).strip().lower() == email.lower():
			return user.get("devices") or []
	return None


def _create_softphone_user_mapping(token, settings, agent, email, devices):
	phones = [cstr(d.get("contact_uri")) for d in devices if d.get("type") == "tel" and d.get("contact_uri")]
	if not phones:
		frappe.throw(
			_("Exotel user {0} has no phone device. Add their mobile in the Exotel dashboard.").format(email)
		)
	if not agent.exotel_number:
		frappe.throw(_("Exotel Number is required to enable the browser softphone."))
	response = _softphone_request(
		"POST",
		"/usermapping",
		token,
		json=[
			{
				"AppUserId": email,
				"AppUsername": email,
				"Email": email,
				"ExotelAccountSid": settings.account_sid,
				"ExotelUserName": frappe.db.get_value("User", agent.user, "full_name") or email,
				# Exotel refused 10-digit numbers here in UAT; the 0-prefixed 11-digit form is accepted.
				"AgentNumber": "0" + last_ten_digits(phones[0]),
				"VirtualNumber": agent.exotel_number,
			}
		],
	)
	# 409 = mapping already exists for this AppUserId/email, which is the state we want.
	if response.status_code not in (200, 409):
		frappe.log_error(title="Exotel softphone user mapping failed", message=response.text)
		frappe.throw(_("Could not map {0} to the Exotel softphone app.").format(email))


def _get_softphone_user_mapping(token, email):
	"""Return the user's mapping, or None only when Exotel says there is none.

	Any other failure raises: treating it as "no mapping" would create one.
	"""
	response = _softphone_request("GET", "/usermapping", token, params={"user_id": email})
	try:
		payload = response.json()
	except ValueError:
		payload = None
	# Integration Core reports "not found" as HTTP 404, or as HTTP 200 with Code 404 in the body.
	if response.status_code == 404 or (isinstance(payload, dict) and payload.get("Code") == 404):
		return None
	data = payload.get("Data") if isinstance(payload, dict) else None
	if not response.ok or not isinstance(data, dict) or not data:
		frappe.log_error(title="Exotel softphone mapping lookup failed", message=response.text)
		frappe.throw(_("Could not read the Exotel softphone mapping. Try again later."))
	return data


def _softphone_api_base():
	host = cstr(frappe.db.get_single_value("CRM Exotel Settings", "softphone_api_host")).strip()
	host = host.removeprefix("https://").removeprefix("http://").strip("/") or DEFAULT_SOFTPHONE_API_HOST
	return f"https://{host}/v2/integrations"


def _get_agent_email(agent):
	return frappe.db.get_value("User", agent.user, "email") or agent.user


def _get_configured_softphone_token(settings):
	app_id = cstr(settings.softphone_app_id).strip()
	app_secret = settings.get_password("softphone_app_secret", raise_exception=False)
	if not app_id or not app_secret:
		frappe.throw(_("Exotel browser softphone is not configured."), title=_("Softphone Unavailable"))
	return _get_softphone_app_token(app_id, app_secret)


def _softphone_request(method, path, token, **kwargs):
	try:
		response = requests.request(
			method,
			f"{_softphone_api_base()}{path}",
			headers={"Authorization": token},
			timeout=10,
			**kwargs,
		)
	except requests.RequestException:
		frappe.log_error(title="Exotel softphone request failed")
		frappe.throw(_("Could not reach Exotel. Try again later."))
	if response.status_code >= 500:
		frappe.log_error(title="Exotel softphone request failed", message=response.text)
		frappe.throw(_("Exotel returned an error. Try again later."))
	return response


def last_ten_digits(number):
	return "".join(ch for ch in cstr(number) if ch.isdigit())[-10:]


def _get_softphone_app_token(app_id: str, app_secret: str):
	cache_key = f"crm:exotel:softphone-token:{app_id}"
	# expires=True: an expiring key must not be pinned in the per-request local cache.
	if token := frappe.cache().get_value(cache_key, expires=True):
		return token

	try:
		response = requests.post(
			f"{_softphone_api_base()}/token",
			json={"Id": app_id, "Secret": app_secret, "Entity": "app"},
			timeout=10,
		)
		response.raise_for_status()
		payload = response.json()
	except (requests.RequestException, ValueError):
		frappe.log_error(title="Exotel softphone token request failed")
		frappe.throw(
			_("Could not initialize Exotel browser softphone. Try mobile calling or contact an administrator."),
			title=_("Softphone Unavailable"),
		)

	token = (
		payload.get("Data")
		if isinstance(payload, dict) and payload.get("Status") == "Success"
		else None
	)
	if not isinstance(token, str) or not token:
		frappe.log_error(title="Invalid Exotel softphone token response")
		frappe.throw(
			_("Exotel returned an invalid softphone token response."),
			title=_("Softphone Unavailable"),
		)

	frappe.cache().set_value(cache_key, token, expires_in_sec=SOFTPHONE_TOKEN_CACHE_SECONDS)
	return token


def _validate_softphone_reference(reference_doctype, reference_docname):
	if not reference_doctype and not reference_docname:
		return
	if reference_doctype not in {"CRM Lead", "CRM Deal"} or not reference_docname:
		frappe.throw(_("Invalid call reference."))
	if not frappe.has_permission(reference_doctype, "read", reference_docname):
		frappe.throw(_("Not permitted to access this call reference."), frappe.PermissionError)


def _validate_softphone_call_owner(call_sid):
	call_log = frappe.get_doc("CRM Call Log", call_sid)
	handlers = {handler for handler in (call_log.caller, call_log.receiver) if handler}
	if handlers and frappe.session.user not in handlers:
		frappe.throw(_("Not permitted to access this call."), frappe.PermissionError)
	return handlers


def _claim_existing_softphone_call(call_sid):
	# A webhook can create the log before the browser registers it. The browser still vouched
	# for this CallSid, so the log gets the same strict SIP verification as one it created.
	# Logs with no handler are left alone: flagging them would let any agent get them failed.
	handlers = _validate_softphone_call_owner(call_sid)
	if frappe.session.user in handlers:
		frappe.db.set_value("CRM Call Log", call_sid, "is_softphone_call", 1, update_modified=False)


def get_exotel_endpoint(action=None, version="v1"):
	settings = get_exotel_settings()
	return "https://{api_key}:{api_token}@{subdomain}/{version}/Accounts/{sid}/{action}".format(
		api_key=settings.api_key,
		api_token=settings.get_password("api_token"),
		subdomain=settings.subdomain,
		version=version,
		sid=settings.account_sid,
		action=action,
	)


def get_all_exophones():
	endpoint = get_exotel_endpoint("IncomingPhoneNumbers", "v2_beta")
	response = requests.get(endpoint)
	return [phone.get("friendly_name") for phone in response.json().get("incoming_phone_numbers", [])]


def get_status_updater_url():
	from frappe.utils.data import get_url

	webhook_verify_token = frappe.db.get_single_value("CRM Exotel Settings", "webhook_verify_token")
	return get_url(f"api/method/crm.integrations.exotel.handler.handle_request?key={webhook_verify_token}")


def get_exotel_settings():
	return frappe.get_single("CRM Exotel Settings")


def validate_request():
	# workaround security since exotel does not support request signature
	# /api/method/<exotel-integration-method>?key=<exotel-webhook=verify-token>
	webhook_verify_token = frappe.db.get_single_value("CRM Exotel Settings", "webhook_verify_token")
	key = frappe.request.args.get("key")
	is_valid = key and key == webhook_verify_token

	if not is_valid:
		frappe.throw(_("Unauthorized request"), exc=frappe.PermissionError)


@frappe.whitelist()
def is_integration_enabled():
	return frappe.db.get_single_value("CRM Exotel Settings", "enabled", True)


# Call Log Functions
def create_call_log(
	call_id,
	from_number,
	to_number,
	medium,
	agent,
	status="Ringing",
	call_type="Incoming",
	reference_doctype=None,
	reference_docname=None,
	is_softphone_call=False,
):
	call_log = frappe.new_doc("CRM Call Log")
	call_log.id = call_id
	call_log.to = to_number
	call_log.medium = medium
	call_log.type = call_type
	call_log.status = status
	call_log.telephony_medium = "Exotel"
	call_log.is_softphone_call = 1 if is_softphone_call else 0
	setattr(call_log, "from", from_number)

	if call_type == "Incoming":
		call_log.receiver = agent
	else:
		call_log.caller = agent

	# Prefer caller-supplied lead/deal context (set when call is initiated from a lead/deal page).
	# Falls back to phone-number lookup via link() for incoming calls and outgoing calls without context.
	if reference_doctype and reference_docname:
		call_log.link_with_reference_doc(reference_doctype, reference_docname)
	else:
		contact_number = from_number if call_type == "Incoming" else to_number
		link(contact_number, call_log)

	# reference_doctype defaults to "CRM Lead"; without a docname that is a dangling half-reference.
	# insert() re-applies defaults to empty fields unless they are listed in dont_update_if_missing.
	if not call_log.reference_docname:
		call_log.reference_doctype = None
		call_log.dont_update_if_missing.append("reference_doctype")

	call_log.save(ignore_permissions=True)
	frappe.db.commit()
	return call_log


def link(contact_number, call_log):
	contact = get_contact_by_phone_number(contact_number)
	if contact.get("name"):
		doctype = "Contact"
		docname = contact.get("name")
		if contact.get("lead"):
			doctype = "CRM Lead"
			docname = contact.get("lead")
		elif contact.get("deal"):
			doctype = "CRM Deal"
			docname = contact.get("deal")
		call_log.link_with_reference_doc(doctype, docname)


def get_call_log(call_payload):
	call_log_id = call_payload.get("CallSid")
	if frappe.db.exists("CRM Call Log", call_log_id):
		return frappe.get_doc("CRM Call Log", call_log_id)


CALL_LOG_TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "Call Not Answered", "Canceled"}

# Integration Core (softphone) status words. Only "completed" and the active state are confirmed
# from captured callbacks; the rest follow Exotel's classic Calls API vocabulary.
INTEGRATION_CORE_STATUS_MAP = {
	"completed": "Completed",
	"in-progress": "In Progress",
	"no-answer": "Call Not Answered",
	"missed": "Call Not Answered",
	# Outbound customer leg that never connected; Exotel sends it for rejected calls too.
	"to_leg_unanswered": "Call Not Answered",
	# The agent's browser never took its own leg, so the customer was never dialled.
	"from_leg_unanswered": "Failed",
	# Agent hung up in the CRM while the customer was still ringing.
	"from_leg_cancelled": "Canceled",
	"from_leg_canceled": "Canceled",
	"busy": "Busy",
	"failed": "Failed",
	"canceled": "Canceled",
	"cancelled": "Canceled",
}


def is_integration_core_payload(call_payload):
	return any(key in call_payload for key in ("AppUserID", "CallState", "CallStatus"))


def normalize_direction(direction):
	direction = (direction or "").lower()
	if direction in ("inbound", "incoming"):
		return "incoming"
	if direction == "outbound":
		return "outbound-dial"
	return direction


def normalize_call_payload(call_payload):
	payload = frappe._dict(call_payload.copy())
	if not is_integration_core_payload(payload):
		return payload

	payload.Direction = normalize_direction(payload.get("Direction"))
	outgoing = payload.Direction.startswith("outbound")

	payload.AgentEmail = payload.get("AgentEmail") or payload.get("AppUserID")
	payload.CallFrom = payload.get("CallFrom") or payload.get("FromNumber")
	if outgoing:
		payload.To = payload.get("ToNumber") or payload.get("To")
	else:
		payload.To = payload.get("To") or payload.get("VirtualNumber") or payload.get("ToNumber")
	payload.RecordingUrl = payload.get("RecordingUrl") or payload.get("CallRecordings")
	duration = payload.get("ConversationDuration") or payload.get("TotalDuration")
	payload.ConversationDuration = float(duration) if duration not in (None, "") else 0
	payload.StartTime = normalize_exotel_datetime(payload.get("StartTime"))
	payload.EndTime = normalize_exotel_datetime(payload.get("EndTime"))

	status = (payload.get("Status") or payload.get("CallStatus") or "").lower()
	if not status and payload.get("CallState") == "active":
		status = "in-progress"
	payload.Status = status
	if payload.Direction == "incoming" and status == "busy":
		# Inbound notifications report agent state: "busy" means the agent is being rung.
		payload.CallLogStatus = "Ringing"
	else:
		payload.CallLogStatus = INTEGRATION_CORE_STATUS_MAP.get(status)
	if outgoing and payload.CallLogStatus is None and payload.get("CallState") == "terminal":
		# Otherwise the log stays In Progress until reconcile; surface new Exotel values instead.
		frappe.log_error(title=f"Unmapped Exotel softphone status: {status or '(empty)'}")
	return payload


def normalize_exotel_datetime(value):
	if not value:
		return None
	value = get_datetime(value)
	if value.tzinfo:
		value = value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return value


def get_call_log_status(call_payload, direction="inbound"):
	if "CallLogStatus" in call_payload:
		return call_payload.CallLogStatus

	if direction == "outbound-api" or direction == "outbound-dial":
		status = call_payload.get("Status")
		if status == "completed":
			return "Completed"
		elif status == "in-progress":
			return "In Progress"
		elif status == "busy":
			return "Ringing"
		elif status == "no-answer":
			return "Call Not Answered"
		elif status == "failed":
			return "Failed"

	call_type = call_payload.get("CallType")
	status = call_payload.get("DialCallStatus") or call_payload.get("Status")

	if call_type == "incomplete" and status == "no-answer":
		status = "Call Not Answered"
	elif call_type == "client-hangup" and status == "canceled":
		status = "Canceled"
	elif call_type == "incomplete" and status == "failed":
		status = "Failed"
	elif call_type == "incomplete" and status == "busy":
		# Agent declined (Leg CauseCode USER_BUSY); the bare "busy" branch below is a mid-call state.
		status = "Busy"
	elif call_type == "completed":
		status = "Completed"
	elif status == "busy":
		status = "Ringing"

	return status or "Ringing"


RECONCILE_MIN_AGE_MINUTES = 5
RECONCILE_MAX_AGE_DAYS = 7
RECONCILE_NOT_FOUND_FAIL_AFTER_MINUTES = 60
RECONCILE_LEG_SETTLE_MINUTES = 15
RECONCILE_BATCH_SIZE = 50
# Recording lookups get their own small budget so they never crowd out status repairs.
RECONCILE_RECORDING_BATCH_SIZE = 10
# Enough candidates that logs waiting on their backoff don't hide ones that are due.
RECONCILE_CANDIDATE_LIMIT = 500
# A log that stays eligible after a run is retried after 5, 10, 20, … minutes, up to this cap, so a
# few stuck logs can't take the whole budget every run.
RECONCILE_MAX_BACKOFF_MINUTES = 6 * 60
# Exotel publishes recordings a few minutes after the call (RecordingAvailableBy); calls without
# recording enabled never get one, so stop looking after this window.
RECONCILE_RECORDING_WINDOW_HOURS = 2
CALLS_API_STATUS_MAP = {**INTEGRATION_CORE_STATUS_MAP, "ringing": "Ringing", "queued": "Queued"}
CALL_API_FINAL_STATUSES = {"completed", "no-answer", "busy", "failed", "canceled"}


def reconcile_stale_call_logs():
	"""Complete Exotel call logs whose status webhooks never arrived, using the Calls API."""
	if not frappe.db.get_single_value("CRM Exotel Settings", "enabled"):
		return

	from frappe.utils.file_lock import LockTimeoutError
	from frappe.utils.synchronization import filelock

	try:
		with filelock("exotel_call_log_reconcile", timeout=1):
			_reconcile_stale_call_logs()
	except LockTimeoutError:
		return


def _reconcile_stale_call_logs():
	now = now_datetime()
	min_age = add_to_date(now, minutes=-RECONCILE_MIN_AGE_MINUTES)
	stale_logs = frappe.get_all(
		"CRM Call Log",
		filters={
			"telephony_medium": "Exotel",
			# Failed logs have no end_time by nature (incl. ones this job failed for missing calls).
			"status": ["!=", "Failed"],
			"creation": ["between", [add_to_date(now, days=-RECONCILE_MAX_AGE_DAYS), min_age]],
		},
		# Terminal logs still qualify while end_time is missing: a Passthru can finish a call
		# with Exotel's epoch EndTime placeholder, and only the Calls API has the real one.
		or_filters={
			"status": ["in", ["Initiated", "Ringing", "In Progress", "Queued"]],
			"end_time": ["is", "not set"],
		},
		fields=["name", "creation"],
		order_by="creation asc",
		limit=RECONCILE_CANDIDATE_LIMIT,
	)
	awaiting_recording = frappe.get_all(
		"CRM Call Log",
		filters={
			"telephony_medium": "Exotel",
			"status": "Completed",
			"recording_url": ["is", "not set"],
			"creation": ["between", [add_to_date(now, hours=-RECONCILE_RECORDING_WINDOW_HOURS), min_age]],
		},
		fields=["name", "creation"],
		order_by="creation asc",
		limit=RECONCILE_CANDIDATE_LIMIT,
	)
	status_batch = [log for log in stale_logs if _reconcile_due(log.name)][:RECONCILE_BATCH_SIZE]
	in_status_batch = {log.name for log in status_batch}
	recording_batch = [
		log for log in awaiting_recording if log.name not in in_status_batch and _reconcile_due(log.name)
	][:RECONCILE_RECORDING_BATCH_SIZE]
	for log in status_batch + recording_batch:
		try:
			reconcile_call_log(log.name, log.creation)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Exotel call log reconcile failed: {log.name}")
		# Logs that got resolved drop out of the selection; the rest wait before the next try.
		_schedule_next_reconcile(log.name)


def _reconcile_backoff_key(call_sid):
	return f"crm:exotel:reconcile-backoff:{call_sid}"


def _reconcile_due(call_sid):
	state = frappe.cache.get_value(_reconcile_backoff_key(call_sid), expires=True)
	return not state or state["next_at"] <= now_datetime().timestamp()


def _schedule_next_reconcile(call_sid):
	key = _reconcile_backoff_key(call_sid)
	attempts = ((frappe.cache.get_value(key, expires=True) or {}).get("attempts") or 0) + 1
	delay_minutes = min(RECONCILE_MIN_AGE_MINUTES * 2 ** (attempts - 1), RECONCILE_MAX_BACKOFF_MINUTES)
	frappe.cache.set_value(
		key,
		{"attempts": attempts, "next_at": now_datetime().timestamp() + delay_minutes * 60},
		expires_in_sec=RECONCILE_MAX_AGE_DAYS * 24 * 60 * 60,
	)


def reconcile_call_log(call_sid, created_at):
	call = fetch_exotel_call(call_sid)
	if call is None:
		if created_at < add_to_date(now_datetime(), minutes=-RECONCILE_NOT_FOUND_FAIL_AFTER_MINUTES):
			status = frappe.db.get_value("CRM Call Log", call_sid, "status")
			# A webhook already finished this log, so the call exists; the lookup itself is failing.
			if status in CALL_LOG_TERMINAL_STATUSES:
				frappe.log_error(
					title="Exotel call not found during reconcile",
					message=f"CRM Call Log {call_sid} ({status}) has no matching Exotel call; status kept.",
				)
				return
			frappe.db.set_value("CRM Call Log", call_sid, "status", "Failed")
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
			frappe.log_error(
				title="Exotel call not found during reconcile",
				message=f"CRM Call Log {call_sid} has no matching Exotel call; marked Failed.",
			)
		return

	# Exotel fills in the agent's SIP leg shortly after the call; until then an inbound record still
	# shows the Exophone, so judging ownership that early would fail every inbound call.
	if agent_leg_pending(call):
		return

	if not softphone_call_matches_log(call_sid, call):
		frappe.db.set_value("CRM Call Log", call_sid, "status", "Failed")
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		frappe.log_error(
			title="Exotel softphone call does not match its call log",
			message=f"CRM Call Log {call_sid}: Exotel From={call.get('From')} To={call.get('To')}; marked Failed.",
		)
		return

	correct_inbound_caller(call_sid, call)

	# Exotel fills Duration/EndTime/leg details asynchronously (~2 min after the call ends). Deciding
	# before then could lock an answered call as missed, so wait for a later run.
	if (call.get("Status") or "").lower() in CALL_API_FINAL_STATUSES and not valid_exotel_end_time(
		call.get("EndTime")
	):
		return

	details = call.get("Details") or {}
	duration = details.get("ConversationDuration")
	payload = frappe._dict(
		CallSid=call_sid,
		Direction=normalize_direction(call.get("Direction")),
		CallLogStatus=get_calls_api_call_log_status(call),
		ConversationDuration=duration if duration is not None else call.get("Duration"),
		StartTime=call.get("StartTime") or None,
		EndTime=valid_exotel_end_time(call.get("EndTime")),
		RecordingUrl=call.get("RecordingUrl"),
	)
	update_call_log(payload)


def correct_inbound_caller(call_sid, call):
	"""Replace a browser-supplied caller number with Exotel's, and re-link the call log to match.

	The ownership check has already passed, so the call did ring this agent; only the number
	the browser reported can be wrong.
	"""
	log = frappe.db.get_value("CRM Call Log", call_sid, ["type", "from", "is_softphone_call"], as_dict=True)
	if not log or not log.is_softphone_call or log.type != "Incoming":
		return
	exotel_from = cstr(call.get("From")).strip()
	if not exotel_from or last_ten_digits(exotel_from) == last_ten_digits(log.get("from")):
		return

	call_log = frappe.get_doc("CRM Call Log", call_sid)
	frappe.log_error(
		title="Exotel softphone caller corrected",
		message=f"CRM Call Log {call_sid}: browser reported {call_log.get('from')}, Exotel says {exotel_from}.",
	)
	call_log.set("from", exotel_from)
	# Drop only the caller's Lead/Deal/Contact links; the agent's notes and tasks stay on the call.
	call_log.set("links", [row for row in call_log.links if row.link_doctype in ("FCRM Note", "CRM Task")])
	call_log.reference_doctype = None
	call_log.reference_docname = None
	link(exotel_from, call_log)
	call_log.save(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def agent_leg_pending(call):
	"""True while an inbound call's record may still gain its agent SIP leg: during the call and
	for a settling window after Exotel's end time (the log's creation time says nothing about it)."""
	if normalize_direction(call.get("Direction")) != "incoming":
		return False
	if cstr(call.get("To")).lower().startswith("sip:"):
		return False
	ended = normalize_exotel_datetime(valid_exotel_end_time(call.get("EndTime")))
	return not ended or ended > add_to_date(now_datetime(), minutes=-RECONCILE_LEG_SETTLE_MINUTES)


def softphone_call_matches_log(call_sid, call):
	"""Check a browser-registered log against Exotel's record of the call.

	The browser supplies the CallSid and number, so such a log must match a SIP leg of the
	registering agent. Server-created logs (webhooks, click-to-call) are trusted.
	"""
	log = frappe.db.get_value(
		"CRM Call Log", call_sid, ["type", "caller", "receiver", "to", "is_softphone_call"], as_dict=True
	)
	if not log or not log.is_softphone_call:
		return True
	agent = log.caller if log.type == "Outgoing" else log.receiver
	expected_sip = cstr(frappe.db.get_value("CRM Telephony Agent", {"user": agent}, "exotel_sip_id")).lower()
	caller_leg, callee_leg = cstr(call.get("From")).lower(), cstr(call.get("To")).lower()
	if not expected_sip:
		return False
	if log.type == "Outgoing":
		return caller_leg == expected_sip and last_ten_digits(callee_leg) == last_ten_digits(log.to)
	return callee_leg == expected_sip


def get_calls_api_call_log_status(call):
	status = (call.get("Status") or "").lower()
	details = call.get("Details") or {}
	if normalize_direction(call.get("Direction")) != "incoming":
		# The Calls API reports an agent hanging up during ringing as "failed"; the customer leg says canceled.
		if status == "failed" and (details.get("Leg2Status") or "").lower() == "canceled":
			return "Canceled"
		# The agent leg failed before the customer was dialled (Webhook: from_leg_unanswered).
		if not details.get("Leg2Status") and (details.get("Leg1Status") or "").lower() in ("no-answer", "failed"):
			return "Failed"
		return CALLS_API_STATUS_MAP.get(status)
	if status != "completed":
		return CALLS_API_STATUS_MAP.get(status)

	# An inbound call is "completed" from the caller's side even when no agent picked up;
	# the agent leg (Leg2) and conversation time say whether it was actually answered.
	conversation = details.get("ConversationDuration") or 0
	agent_leg = (details.get("Leg2Status") or "").lower()
	if agent_leg == "completed" or float(conversation) > 0:
		return "Completed"
	# Agent rejected in the softphone; shown as "Declined" in the UI, separate from a missed call.
	if agent_leg == "busy":
		return "Busy"
	return "Call Not Answered"


def fetch_exotel_call(call_sid, timeout=10):
	response = requests.get(get_exotel_endpoint(f"Calls/{call_sid}.json?details=true"), timeout=timeout)
	if response.status_code == 404:
		return None
	response.raise_for_status()
	return response.json().get("Call")


def get_call_log_to_number(call_payload):
	# For inbound calls DialWhomNumber is the agent's device (e.g. a SIP ID), not the number dialled.
	if (call_payload.get("Direction") or "").startswith("outbound"):
		return call_payload.get("DialWhomNumber") or call_payload.get("To")
	return call_payload.get("To")


def valid_exotel_end_time(value):
	# Flow Passthru sends the Unix epoch (rendered in IST) as EndTime while the call is wrapping up.
	if not value or str(value).startswith("1970-01-01"):
		return None
	return value


def is_valid_call_log_status(status):
	options = frappe.get_meta("CRM Call Log").get_field("status").options or ""
	return bool(status) and status in options.split("\n")


def update_call_log(call_payload, call_log=None):
	direction = call_payload.get("Direction")
	call_log = call_log or get_call_log(call_payload)
	status = get_call_log_status(call_payload, direction)
	if not call_log:
		return

	if not is_valid_call_log_status(status):
		frappe.logger("exotel").warning(
			f"[Exotel] ignoring unknown status {status!r} for CallSid={call_payload.get('CallSid')}"
		)
		status = None

	updates = {
		"status": status,
		"to": get_call_log_to_number(call_payload),
		# DialCallDuration includes ring time; the dialled leg's OnCallDuration is talk time.
		"duration": 0
		if call_payload.get("CallType") == "incomplete"
		else call_payload.get("Legs[0][OnCallDuration]")
		or call_payload.get("DialCallDuration")
		or call_payload.get("ConversationDuration")
		or 0,
		"start_time": call_payload.get("StartTime"),
		"end_time": valid_exotel_end_time(call_payload.get("EndTime")),
	}

	# Only set recording_url when Exotel provides one — never overwrite
	# an existing URL with an empty string from an intermediate event.
	if call_payload.get("RecordingUrl"):
		updates["recording_url"] = call_payload.get("RecordingUrl")

	if direction == "incoming" and call_payload.get("AgentEmail"):
		updates["receiver"] = call_payload.get("AgentEmail")

	# Exotel retries and reorders callbacks, so a stale "answered" can arrive after "completed".
	# A terminal log keeps its status and number; later callbacks may only fill gaps.
	terminal = call_log.status in CALL_LOG_TERMINAL_STATUSES
	if terminal:
		updates = {
			field: value
			for field, value in updates.items()
			if field not in ("status", "to", "receiver") and not call_log.get(field) and value
		}

	changed = False
	for field, value in updates.items():
		if value is None:
			continue
		current_value = call_log.get(field)
		if current_value == value or str(current_value or "") == str(value or ""):
			continue
		call_log.set(field, value)
		changed = True

	if not changed:
		return

	call_log.save(ignore_permissions=True)
	# Webhook handler runs outside request transaction; explicit commit so call log
	# survives downstream errors in subsequent processing.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return call_log

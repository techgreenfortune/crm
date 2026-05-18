import time

import frappe
import requests

_RETRY_AFTER_CAP_SECONDS = 5.0
_DEFAULT_TIMEOUT_SECONDS = 10
_CREATE_PROJECT_PATH = "/api/external/frappe/projects"


def _get_settings():
	return frappe.get_single("CRM Project API Settings")


def _resolve_sales_person_email(lead_name: str) -> str:
	"""Return the email of the earliest-assigned user that holds Sales Manager.

	Frappe stores per-lead assignments in ``ToDo`` (one row per allocation).
	A lead can have several open assignees; the backend's
	``getSalesManagerIdByEmailQuery`` resolves a single email → sales_manager_id,
	so we have to choose one. The contract: walk the open ToDos in
	``creation ASC`` order (first assigned first) and return the first
	``allocated_to`` whose user carries the "Sales Manager" role.

	Returns "" when no assignee qualifies — the backend handles that gracefully
	(leaves ``sales_manager_id`` NULL with a warn-level log).
	"""
	assignees = frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "CRM Lead",
			"reference_name": lead_name,
			"status": ["!=", "Cancelled"],
		},
		fields=["allocated_to"],
		order_by="creation asc",
		pluck="allocated_to",
	)
	for email in assignees:
		if not email:
			continue
		if "Sales Manager" in frappe.get_roles(email):
			return email
	return ""


def _post_with_retry(url: str, json: dict, headers: dict, timeout: float):
	"""POST with a single retry on HTTP 429.

	Mirrors crm.integrations.brevo.api._post_with_retry — caller's existing
	4xx/5xx handling works unchanged; this only adds one retry on rate-limit.
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


def _extract_project_id(body: dict) -> str | None:
	"""Pull the created project_id out of the Node controller's response shape.

	Successful response (frappe.controller.ts -> sendSuccess) is:
	  { success: true, message: "...", data: { project: {...}, idempotent: bool } }
	but we tolerate a flatter { project: {...} } in case the wrapper changes.
	"""
	if not isinstance(body, dict):
		return None
	project = (body.get("data") or {}).get("project") or body.get("project") or {}
	pid = project.get("project_id") or project.get("id")
	return str(pid) if pid else None


@frappe.whitelist()
def create_project_on_won(lead_name: str) -> None:
	"""Fire POST /api/external/frappe/projects for a lead that has reached C8.

	Whitelisted because the CRM Lead After-Save server script enqueues this via
	frappe.enqueue(...). Frappe's RestrictedPython gate on enqueue targets
	requires the destination to be whitelisted.

	Idempotency: lead.custom_external_project_id is the local short-circuit;
	external_id=lead.name lets the backend's unique (external_source, external_id)
	index serve as the second line of defence on concurrent re-fires.
	"""
	lead = frappe.get_doc("CRM Lead", lead_name)

	if lead.status != "C8":
		# Defensive: a downstream race could move the lead off C8 between the
		# enqueue and the worker dequeue. Skip silently — when it returns to C8
		# the After-Save script will re-enqueue.
		return
	if lead.get("custom_external_project_id"):
		return

	settings = _get_settings()
	if not settings.enabled:
		return

	api_key = settings.get_password("api_key")
	base_url = (settings.api_base_url or "").rstrip("/")
	if not (api_key and base_url):
		frappe.log_error(
			f"Lead {lead_name}: CRM Project API Settings incomplete (api_base_url or api_key missing).",
			"Project API: misconfigured",
		)
		return

	timeout = int(settings.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)

	# Site location is a "<lat>,<lng>" string. Send it only when both halves
	# are populated — a half-populated coord is misleading downstream.
	lat = lead.get("custom_latitude")
	lng = lead.get("custom_longitude")
	site_location = (
		f"{lat},{lng}" if (lat is not None and lat != "" and lng is not None and lng != "") else None
	)

	# B1 — order_type derived from the lead's Retail/Projects flag. Backend
	# validator accepts only lowercase "retail" or "project" (singular).
	order_type = "retail" if lead.get("custom_lead_type") == "Retail" else "project"

	# B2 + B3 — Prefer the full street address; fall back to the Area locality
	# (Link → name) for retail leads. Project leads reach this code only after
	# validate_project_specific_fields confirmed the full address is set, so the
	# fallback path is effectively retail-only.
	site_address = lead.get("custom_site_address_full") or (
		str(lead.custom_area) if lead.get("custom_area") else None
	)

	# B4 (partial split) — Project site pincode (custom_site_pincode) is the
	# project-only field; billing pincode stays as custom_pincode. For retail
	# leads (no site pincode field) both fall back to custom_pincode.
	site_pincode = lead.get("custom_site_pincode") or lead.get("custom_pincode") or None

	# B6 — Don't pollute alternate_number with a duplicate of mobile_no.
	alt_number = lead.phone if (lead.phone and lead.phone != lead.mobile_no) else ""

	# Payload shape matches the receiver exactly — every field below is read by
	# either validateCreateProjectV2 (top-level) or createProjectFromFrappe's
	# customer block. Anything not read by the controller has been dropped.
	#
	#   Required:        external_id, name, order_type
	#   Top-level opt:   source, pin_code, site_address, site_location,
	#                    project_category, project_configuration,
	#                    sales_person_email
	#   Customer block:  customer_name, customer_mobile (controller mandatory);
	#                    customer_email / customer_pincode / customer_city /
	#                    customer_state / customer_address (controller has
	#                    placeholder fallbacks); customer_profession,
	#                    customer_alternate_number, customer_gst_number (opt).
	#
	# gst_applicable is intentionally NOT sent — the backend derives it from
	# customer_gst_number presence to avoid drift between the two signals.
	payload = {
		"external_id": lead.name,
		# B5 — no fallback to lead.name (internal ID). Empty string lets backend
		# 400 cleanly; handler's existing failure path logs + Comment-on-lead.
		"name": lead.lead_name or "",
		"order_type": order_type,
		"source": lead.source or None,
		"pin_code": site_pincode,
		"site_address": site_address,
		"site_location": site_location,
		"project_category": lead.get("custom_project_category") or None,
		"project_configuration": lead.get("custom_project_configuration") or None,
		"sales_person_email": _resolve_sales_person_email(lead.name),
		"customer": {
			"customer_name": lead.lead_name or "",
			"customer_mobile": lead.mobile_no or "",
			"customer_email": lead.email or "",
			# Billing pincode — unchanged. Distinct from top-level pin_code (site).
			"customer_pincode": lead.get("custom_pincode") or "",
			"customer_city": lead.get("custom_city") or "",
			"customer_state": lead.get("custom_state") or "",
			# Mirror the site address into the customer block until a billing-
			# address field exists. Empty string lets backend's safeAddress kick in.
			"customer_address": site_address or "",
			"customer_profession": lead.get("custom_customer_type") or "",
			"customer_alternate_number": alt_number,
			"customer_gst_number": lead.get("custom_gst_number") or "",
		},
	}
	headers = {
		"accept": "application/json",
		"content-type": "application/json",
		"x-api-key": api_key,
	}

	response = _post_with_retry(
		f"{base_url}{_CREATE_PROJECT_PATH}",
		json=payload,
		headers=headers,
		timeout=timeout,
	)

	if not response.ok:
		# Trim the response body — backend validator errors often echo request
		# payload fragments (customer email, etc.) which we don't want spilled
		# verbatim into Error Log. 500 chars is enough to diagnose any backend
		# error without leaking the whole payload.
		frappe.log_error(
			f"Lead {lead_name}: status {response.status_code} — {response.text[:500]}",
			"Project API: create failed",
		)
		# Audit trail on the lead so ops sees the failure without trawling Error Log.
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": (
						f"[AUTOMATION] External Project create failed "
						f"(HTTP {response.status_code}). Manual re-trigger may be required."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass
		return

	try:
		body = response.json()
	except ValueError:
		body = {}

	project_id = _extract_project_id(body)
	if project_id:
		frappe.db.set_value(
			"CRM Lead",
			lead_name,
			"custom_external_project_id",
			project_id,
		)

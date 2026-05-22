import time

import frappe
import requests

_RETRY_AFTER_CAP_SECONDS = 5.0
_DEFAULT_TIMEOUT_SECONDS = 10
# OpsGate's public Frappe-handoff endpoint (renamed from the legacy
# /api/external/frappe/projects on 2026-05-22). Mounted before verifyToken
# in src/routes/v2.routes.ts; auth is via X-Api-Secret + FRAPPE_CRM_SECRET.
_CREATE_PROJECT_PATH = "/api/v2/projects/public"


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


def _extract_project_id(body: dict) -> tuple[str | None, bool]:
	"""Return ``(project_id, idempotent)`` from the controller's response shape.

	V2 public endpoint (project.controller.ts → createProjectPublic) returns:
	  201 { project: {...}, idempotent: false } on create
	  200 { project: {...}, idempotent: true  } on retry of a known external_id

	We also tolerate the older ``data``-wrapped shape (``{ data: { project: {...},
	idempotent: bool } }``) in case the response goes through a wrapper layer —
	cheap forward-compatibility for the transition window.
	"""
	if not isinstance(body, dict):
		return None, False
	# Prefer the flat shape; fall back to the wrapped one.
	scope = body if "project" in body else (body.get("data") or {})
	project = scope.get("project") or {}
	pid = project.get("project_id") or project.get("id")
	idempotent = bool(scope.get("idempotent"))
	return (str(pid) if pid else None), idempotent


@frappe.whitelist()
def create_project_on_won(lead_name: str) -> None:
	"""Fire ``POST /api/v2/projects/public`` for a lead that has reached C4 (Won).

	Whitelisted so the manual Create Project handler (`crm.api.projects.create_project_for_lead`)
	can invoke it. Auto-enqueue from After-Save is no longer wired — project creation
	is triggered by an explicit Sales Owner click; see `crm.api.projects`.

	Auth: sends ``X-Api-Secret`` (value taken from ``CRM Project API Settings.api_key``).
	The receiver does a ``timingSafeEqual`` against its ``FRAPPE_CRM_SECRET`` env var;
	missing/wrong → 401 and the handler logs + drops an audit comment on the lead.

	Idempotency: ``lead.custom_external_project_id`` is the local short-circuit; the
	backend uses ``external_id=lead.name`` (unique with the source) as the second line
	of defence and returns 200 + ``idempotent: true`` if it already saw the id.
	"""
	lead = frappe.get_doc("CRM Lead", lead_name)

	if lead.status != "C4":
		# Defensive: a stage change between caller's gate and our run.
		# Skip silently — the caller will surface its own error.
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

	# Resolve the salesperson up front. The V2 public endpoint REQUIRES
	# sales_person_email: it resolves both sales_manager_id and created_by on the
	# backend (no more req.user fallback). Empty would 400; surface a clearer
	# error locally so the user sees "no Sales Manager assigned" instead of a
	# vague HTTP error.
	sales_person_email = _resolve_sales_person_email(lead_name)
	if not sales_person_email:
		frappe.log_error(
			f"Lead {lead_name}: no assignee with the Sales Manager role; cannot "
			f"resolve sales_person_email for the project handoff.",
			"Project API: missing sales_person_email",
		)
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": (
						"[AUTOMATION] Project handoff aborted — no assignee on this "
						"lead carries the Sales Manager role. Assign a Sales Manager "
						"and re-fire."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass
		frappe.throw(
			frappe._(
				"Cannot create project: no assignee on this lead has the Sales Manager "
				"role. Assign a sales manager before clicking Create Project."
			),
			title=frappe._("Sales Manager Required"),
		)

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
		"sales_person_email": sales_person_email,
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
		# Renamed from x-api-key on 2026-05-22. The verifyFrappeSecret middleware
		# on OpsGate matches against FRAPPE_CRM_SECRET; the value lives in
		# CRM Project API Settings.api_key (Password field — name kept stable to
		# avoid migrating existing sites; admins set it to the new secret value).
		"X-Api-Secret": api_key,
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

	project_id, idempotent = _extract_project_id(body)
	if project_id:
		frappe.db.set_value(
			"CRM Lead",
			lead_name,
			"custom_external_project_id",
			project_id,
		)
		if idempotent:
			# Backend recognised the external_id from a prior call — useful audit
			# signal for ops if the local short-circuit (custom_external_project_id)
			# ever races a manual edit.
			try:
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "CRM Lead",
						"reference_name": lead_name,
						"content": (
							f"[AUTOMATION] Project API returned existing project "
							f"(idempotent re-fire); external id: {project_id}."
						),
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass

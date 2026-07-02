import json
import time

import frappe
import requests

from crm.overrides.crm_lead_permissions import upstream_user

_RETRY_AFTER_CAP_SECONDS = 5.0
# OpsGate's /v2/projects/public does synchronous S3 download (of the signed
# quote PDF) + DB write before responding, so the response can easily exceed
# the old 10s default. 60s gives comfortable headroom; admins can override
# via CRM OpsGate API Settings.timeout_seconds.
_DEFAULT_TIMEOUT_SECONDS = 60
# OpsGate's public Frappe-handoff endpoint (renamed from the legacy
# /api/external/frappe/projects on 2026-05-22). Mounted before verifyToken
# in src/routes/v2.routes.ts; auth is via X-Api-Secret + FRAPPE_CRM_SECRET.
# Path is relative to `api_base_url` (CRM OpsGate API Settings), which already
# includes the `/api` prefix — same base the SSO flow uses.
_CREATE_PROJECT_PATH = "/v2/projects/public"


def _get_settings():
	return frappe.get_single("CRM OpsGate API Settings")


def _resolve_sales_person_email(lead_name: str) -> str:
	"""Return the email of the senior-most assignee on a lead.

	Frappe stores per-lead assignments in ``ToDo`` (one row per allocation).
	A lead can have several open assignees; the backend's
	``getSalesManagerIdByEmailQuery`` resolves a single email → sales_manager_id,
	so we have to choose one. The contract:

	1. Walk open ToDos in ``creation ASC`` order.
	2. For each assignee, compute their most senior CRM role rank from
	   ``role_config.ROLE_RANK`` (lower rank = more senior).
	3. Return the assignee with the lowest rank — ties broken by creation order
	   (earliest assigned wins).
	4. If no assignee has a recognized CRM role, fall back to ``lead.lead_owner``.
	5. Return "" only when both lookups come up empty — the backend handles that
	   gracefully (leaves ``sales_manager_id`` NULL with a warn-level log).
	"""
	from crm.permissions.role_config import ROLE_RANK

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

	best_rank: int | None = None
	best_email = ""
	for email in assignees:
		if not email:
			continue
		user_rank = min(
			(ROLE_RANK[r] for r in frappe.get_roles(email) if r in ROLE_RANK),
			default=None,
		)
		if user_rank is None:
			continue
		if best_rank is None or user_rank < best_rank:
			best_rank = user_rank
			best_email = email

	if best_email:
		return best_email

	return frappe.db.get_value("CRM Lead", lead_name, "lead_owner") or ""


def _is_dealer_lead(lead_owner: str | None) -> bool:
	"""Dealer-channel = the lead_owner carries the ``Dealer`` role.

	No coupling to ``custom_lead_type`` — a Dealer can own Retail or Projects
	leads; the role assignment is the sole channel marker.
	"""
	if not lead_owner:
		return False
	return "Dealer" in frappe.get_roles(lead_owner)


def _reports_to_email(user: str) -> str | None:
	"""Return the email of ``user``'s immediate manager in CRM Sales Hierarchy."""
	manager = upstream_user(user)
	if not manager:
		return None
	return frappe.db.get_value("User", manager, "email") or manager


def _build_order_block(lead_name: str) -> dict:
	"""Return the order block for a lead, sourced from the latest Quote Request.

	A Won lead must have a Quote Request — that's the source of commercial
	truth handed to OpsGate. If none exists this is a data-integrity bug, so
	throw and abort the handoff rather than send a half-populated project.
	"""
	from crm.api.files import build_signed_file_url

	rows = frappe.get_all(
		"CRM Quote Request",
		filters={"lead": lead_name},
		fields=[
			"name",
			"quote_number",
			"quote_value",
			"quote_sq_ft",
			"total_quantity",
			"quote_file",
		],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		frappe.throw(
			frappe._(
				"Cannot create project: lead {0} has no Quote Request. "
				"Create and accept a quote before handing off to OpsGate."
			).format(lead_name),
			title=frappe._("Quote Request Required"),
		)

	qr = rows[0]
	return {
		"name": qr.name,
		"quotation_number": qr.quote_number or "",
		"order_value": qr.quote_value or 0,
		"area": qr.quote_sq_ft or 0,
		"total_quantity": qr.total_quantity or 0,
		"quote_file_url": build_signed_file_url(qr.quote_file) if qr.quote_file else None,
	}


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

	Auth: sends ``X-Api-Secret`` (value taken from ``CRM OpsGate API Settings.api_key``).
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
	# Base URL comes from CRM OpsGate API Settings — same single source the
	# SSO flow reads, so the two integrations cannot drift onto different
	# OpsGate hosts.
	base_url = (settings.get("api_base_url") or "").rstrip("/")
	if not (api_key and base_url):
		frappe.log_error(
			title="Project API: misconfigured",
			message=(
				f"Lead {lead_name}: project handoff misconfigured "
				f"(api_base_url or api_key in CRM OpsGate API Settings missing)."
			),
		)
		return

	timeout = int(settings.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)

	# Resolve the salesperson up front. The V2 public endpoint REQUIRES
	# sales_person_email: it resolves both sales_manager_id and created_by on the
	# backend (no more req.user fallback). Empty would 400; surface a clearer
	# error locally so the user sees "no sales owner assigned" instead of a
	# vague HTTP error.
	sales_person_email = _resolve_sales_person_email(lead_name)
	if not sales_person_email:
		frappe.log_error(
			title="Project API: missing sales_person_email",
			message=(
				f"Lead {lead_name}: no assignee with a CRM role and no lead_owner; "
				f"cannot resolve sales_person_email for the project handoff."
			),
		)
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": (
						"[AUTOMATION] Project handoff aborted — this lead has no "
						"assignee with a CRM role and no lead_owner. Assign a sales "
						"owner and re-fire."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass
		frappe.throw(
			frappe._(
				"Cannot create project: this lead has no assignee with a CRM role "
				"and no lead_owner. Assign a sales owner before clicking Create Project."
			),
			title=frappe._("Sales Owner Required"),
		)

	# Site location is a "<lat>,<lng>" string. Send it only when both halves
	# are populated AND non-zero. The Float column default-coerces missing
	# lat/lng to 0.0 after any save (the column is NOT NULL DEFAULT 0), so a
	# strict `is not None` test would let every un-geocoded lead through as
	# "0.0,0.0" — bogus coordinates downstream. The (0, 0) false-negative for
	# a hypothetical Null-Island lead is an accepted edge case.
	lat = lead.get("custom_latitude")
	lng = lead.get("custom_longitude")
	has_lat = lat is not None and lat != "" and float(lat) != 0.0
	has_lng = lng is not None and lng != "" and float(lng) != 0.0
	site_location = f"{lat},{lng}" if (has_lat and has_lng) else None

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

	# Order block: the latest CRM Quote Request linked to the lead drives the
	# project's commercial details. "Latest by creation" without a status filter
	# is intentional — if a newer revision exists, OpsGate should see the
	# freshest numbers even if it isn't formally Accepted yet. The quote PDF is
	# shared as a short-lived HMAC-signed URL; OpsGate downloads it once and
	# copies to its own S3.
	order = _build_order_block(lead.name)

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
			"customer_address": lead.get("custom_customer_address") or "",
			"customer_profession": lead.get("custom_customer_type") or "",
			"customer_alternate_number": alt_number,
			"customer_gst_number": lead.get("custom_gst_number") or "",
		},
		"order": order,
	}

	# Affiliate extension — emitted when the lead is tagged as an affiliate
	# lead AND the commission has been approved by a Sales Head.  The gate
	# at ``crm.api.projects.create_project_for_lead`` ensures the unapproved
	# case can't reach here, but we double-check the status before emitting
	# so OpsGate never sees commission data without an explicit approval.
	if lead.get("custom_is_affiliate_lead") and lead.get("custom_affiliate_approval_status") == "Approved":
		affiliate_name = lead.custom_affiliate
		affiliate_doc = (
			frappe.db.get_value(
				"CRM Affiliate",
				affiliate_name,
				["affiliate_name", "email", "mobile_no"],
				as_dict=True,
			)
			or {}
		)
		commission_pct = float(lead.get("custom_affiliate_commission_pct") or 0)
		quote_value = float(order.get("order_value") or 0)
		commission_amount = round(quote_value * commission_pct / 100.0, 2) if commission_pct else 0
		payload["affiliate"] = {
			"affiliate_id": affiliate_name,
			"affiliate_name": affiliate_doc.get("affiliate_name") or affiliate_name,
			"affiliate_email": affiliate_doc.get("email") or "",
			"affiliate_mobile": affiliate_doc.get("mobile_no") or "",
			"commission_percentage": commission_pct,
			"commission_amount": commission_amount,
			"approved_by": lead.get("custom_affiliate_approved_by") or "",
			"approved_at": (
				lead.get("custom_affiliate_approved_at").isoformat()
				if lead.get("custom_affiliate_approved_at")
				else ""
			),
		}

	# Dealer-channel extension — emitted only when the lead_owner carries the
	# Dealer role.  The non-dealer payload shape is unchanged (no new keys),
	# so existing OpsGate consumers are unaffected.
	#
	#   - channel:          "dealer"  → unambiguous channel marker
	#   - dealer_email:     the lead_owner who actually placed the order
	#   - reports_to_email: the lead_owner's immediate manager in
	#                        CRM Sales Hierarchy (one level up)
	#   - sales_person_email is overridden to the reports-to email so the
	#     OpsGate-side sales_manager_id resolves to the manager, not the dealer.
	#
	# If the dealer has no hierarchy row / no parent, we skip the override and
	# emit a Comment on the lead so admins can fix the tree.
	if _is_dealer_lead(lead.lead_owner):
		dealer_email = frappe.db.get_value("User", lead.lead_owner, "email") or lead.lead_owner
		reports_to = _reports_to_email(lead.lead_owner)
		payload["channel"] = "dealer"
		payload["dealer_email"] = dealer_email
		if reports_to:
			payload["reports_to_email"] = reports_to
			payload["sales_person_email"] = reports_to
		else:
			frappe.log_error(
				title="Project API: dealer has no reports-to in hierarchy",
				message=f"lead={lead_name} dealer={lead.lead_owner}",
			)
			try:
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "CRM Lead",
						"reference_name": lead_name,
						"content": (
							"[AUTOMATION] Project handoff: dealer has no manager in "
							"CRM Sales Hierarchy — sales_person_email left as the dealer. "
							"Add the dealer's reports_to and re-fire to route to the manager."
						),
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass

	headers = {
		"accept": "application/json",
		"content-type": "application/json",
		# Renamed from x-api-key on 2026-05-22. The verifyFrappeSecret middleware
		# on OpsGate matches against FRAPPE_CRM_SECRET; the value lives in
		# CRM OpsGate API Settings.api_key (Password field — name kept stable to
		# avoid migrating existing sites; admins set it to the new secret value).
		"X-Api-Secret": api_key,
	}

	frappe.logger("project_api").info(
		"OpsGate handoff payload lead=%s payload=%s",
		lead_name,
		json.dumps(payload, default=str),
	)

	try:
		response = _post_with_retry(
			f"{base_url}{_CREATE_PROJECT_PATH}",
			json=payload,
			headers=headers,
			timeout=timeout,
		)
	except requests.exceptions.RequestException as exc:
		# Network-level failure (timeout, DNS, connection refused, TLS, etc.).
		# Log + audit comment + `frappe.throw` so the user sees the underlying
		# cause in the UI instead of a generic "no project id" message. OpsGate
		# dedupes on external_id, so a manual re-click after the cause is fixed
		# is safe.
		frappe.log_error(
			title="Project API: network error",
			message=f"Lead {lead_name}: network error talking to project API — {type(exc).__name__}: {exc}",
		)
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": (
						f"[AUTOMATION] External Project create failed "
						f"(network error: {type(exc).__name__}). Manual re-trigger may be required."
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass
		frappe.throw(
			frappe._("Could not reach the project API: {0}. Check network and retry.").format(
				type(exc).__name__
			),
			title=frappe._("Project Handoff Failed"),
		)

	if not response.ok:
		# Trim the response body — backend validator errors often echo request
		# payload fragments (customer email, etc.) which we don't want spilled
		# verbatim into Error Log. 500 chars is enough to diagnose any backend
		# error without leaking the whole payload.
		frappe.log_error(
			title="Project API: create failed",
			message=f"Lead {lead_name}: status {response.status_code} — {response.text[:500]}",
		)
		# Pull a user-readable message out of the response — OpsGate returns
		# `{ "error": "...", "message": "..." }` shapes on 4xx; fall back to
		# trimmed raw text so the user always sees *something* actionable.
		opsgate_message = ""
		try:
			err_body = response.json()
			if isinstance(err_body, dict):
				opsgate_message = (
					err_body.get("message") or err_body.get("error") or err_body.get("detail") or ""
				)
		except ValueError:
			pass
		if not opsgate_message:
			opsgate_message = (response.text or "").strip()[:300]

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
						f"(HTTP {response.status_code}): {opsgate_message}"
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			pass

		# Surface OpsGate's actual error to the user instead of the generic
		# "no project id" message from the caller's gate. `frappe.throw` renders
		# the message as a toast/modal in the UI.
		frappe.throw(
			frappe._("Project creation failed (HTTP {0}): {1}").format(
				response.status_code, opsgate_message or frappe._("no error details")
			),
			title=frappe._("Project Handoff Failed"),
		)

	try:
		body = response.json()
	except ValueError:
		body = {}

	project_id, idempotent = _extract_project_id(body)
	if not project_id:
		# 2xx but the response body didn't include a project id in either the
		# flat (`body.project.project_id`/`id`) or wrapped (`body.data.project.*`)
		# shape. Log the raw body trimmed to 1KB so we can fix `_extract_project_id`
		# or surface a backend contract drift. Caller (`create_project_for_lead`)
		# detects the missing id and aborts the Won flip, so this is a recoverable
		# state — user re-clicks once the shape is reconciled.
		import json as _json

		try:
			body_excerpt = _json.dumps(body)[:1024]
		except (TypeError, ValueError):
			body_excerpt = repr(body)[:1024]
		frappe.log_error(
			title="Project API: missing project id in 2xx response",
			message=(
				f"Lead {lead_name}: HTTP {response.status_code} OK but no project id "
				f"in response body. Body excerpt: {body_excerpt}"
			),
		)
		frappe.throw(
			frappe._(
				"Project API returned {0} but no project id. Backend may not have "
				"committed the project — check Error Log for response body and retry."
			).format(response.status_code),
			title=frappe._("Project Handoff Failed"),
		)

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

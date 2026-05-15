"""Public website lead-capture endpoint.

Exposed at: POST https://crm.indiframe.com/api/method/crm.api.website.create_lead

Token setup: see admin-ui-setup-guide.md §10a.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, cast

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import validate_email_address

from crm.utils import parse_phone_number

if TYPE_CHECKING:
	from crm.fcrm.doctype.crm_lead.crm_lead import CRMLead

WEBSITE_SOURCE = "Direct"
WEBSITE_SUB_SOURCE = "Website"
DEFAULT_STATUS = "C0"
DEFAULT_LEAD_STATUS = "Active"
TOKEN_HEADER = "X-IndiFrame-Token"
TOKEN_CONF_KEY = "indiframe_website_token"

PROJECTS_CUSTOMER_TYPES = frozenset({"Architect", "Builder", "Contractor"})


def _derive_lead_type(customer_type: str | None) -> str:
	return "Projects" if customer_type in PROJECTS_CUSTOMER_TYPES else "Retail"


def _verify_token() -> None:
	expected_raw = frappe.conf.get(TOKEN_CONF_KEY)
	if not expected_raw or not isinstance(expected_raw, str):
		frappe.log_error(
			title="Website Lead API misconfigured",
			message=f"{TOKEN_CONF_KEY} is not set in site_config.json",
		)
		frappe.throw(
			_("Website lead capture is not configured."),
			exc=frappe.AuthenticationError,
		)
	expected: str = expected_raw

	received = frappe.get_request_header(TOKEN_HEADER) or ""
	if not hmac.compare_digest(received, expected):
		frappe.throw(
			_("Invalid or missing {0} header.").format(TOKEN_HEADER),
			exc=frappe.AuthenticationError,
		)


def _normalize_phone(raw: str) -> tuple[str, str]:
	"""Return (E.164, national_number) when valid; throw otherwise."""
	parsed = parse_phone_number(raw)
	if not parsed.get("success") or not parsed.get("is_valid"):
		frappe.throw(
			_("Phone number {0} is not a valid mobile number.").format(raw),
			exc=frappe.ValidationError,
		)
	return parsed["formats"]["E164"], parsed["national_number"]


def _split_name(full_name: str) -> tuple[str, str]:
	parts = full_name.strip().split(maxsplit=1)
	if len(parts) == 1:
		return parts[0], ""
	return parts[0], parts[1]


def _phone_candidates(e164: str, national_number: str, raw: str) -> list[str]:
	# Indian-format coverage only (parse_phone_number defaults to "IN").
	# Non-IN legacy numbers will not match; revisit if international leads land.
	return list(
		{
			e164,
			raw.strip(),
			national_number,
			f"0{national_number}",
			f"+91{national_number}",
			f"91{national_number}",
		}
	)


def _find_existing_lead(e164: str, national_number: str, raw: str) -> tuple[str | None, str | None]:
	"""Phone lookup scoped to Open-type CRM Lead Statuses.

	Returns ``(open_match_name, closed_match_status)``. Exactly one of the
	two is non-None on a match; both are None on a fresh number.

	Closed leads (status type != "Open" — i.e. C6, C8, Archived) deliberately
	do NOT count as a dedup hit: the Phone Dedup server script's own message
	directs callers to the Reactivation flow rather than silent re-attribution.
	"""
	candidates = _phone_candidates(e164, national_number, raw)
	open_statuses = frappe.get_all("CRM Lead Status", filters={"type": "Open"}, pluck="name")

	open_match = frappe.db.get_value(
		"CRM Lead",
		{"mobile_no": ["in", candidates], "status": ["in", open_statuses]},
		["name", "status"],
		as_dict=True,
	)
	if open_match:
		return open_match.name, None

	closed_match = frappe.db.get_value(
		"CRM Lead",
		{"mobile_no": ["in", candidates]},
		["name", "status"],
		as_dict=True,
	)
	if closed_match:
		return None, closed_match.status

	return None, None


def _trail_parts(message: str | None, payload: dict) -> list[str]:
	bits: list[str] = []
	if message:
		bits.append(f"Message: {message}")
	if payload.get("city"):
		bits.append(f"City: {payload['city']}")
	if payload.get("project_type"):
		bits.append(f"Project Type: {payload['project_type']}")
	utm = ", ".join(
		f"{k}={payload[k]}"
		for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content")
		if payload.get(k)
	)
	if utm:
		bits.append(f"UTM: {utm}")
	if payload.get("page_url"):
		bits.append(f"Page: {payload['page_url']}")
	return bits


def _clean_email(raw: str | None) -> tuple[str | None, str | None]:
	"""Return (cleaned_email, drop_note); drop_note is set only when input was provided but invalid."""
	if not raw:
		return None, None
	clean = validate_email_address(raw, throw=False)
	if not clean:
		return None, f"Email omitted: '{raw}' failed validation."
	return clean, None


# nosemgrep -- public lead-capture endpoint; allow_guest=True is intentional, protected by rate_limit and input validation
@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=60, seconds=60)
def create_lead(
	name: str | None = None,
	mobile: str | None = None,
	email: str | None = None,
	pincode: str | None = None,
	city: str | None = None,
	company: str | None = None,
	message: str | None = None,
	customer_type: str | None = None,
	lead_type: str | None = None,  # ignored; custom_lead_type is derived from customer_type
	project_type: str | None = None,
	utm_source: str | None = None,
	utm_medium: str | None = None,
	utm_campaign: str | None = None,
	utm_content: str | None = None,
	page_url: str | None = None,
) -> dict:
	"""Create or re-attribute a CRM Lead from an indiframe.com form submission.

	The website (indiframe-web) is responsible for sending canonical snake_case
	keys and properly-cased Select values (`Homeowner` not `homeowner`, etc.).
	This endpoint does not perform alias / case normalization.

	Returns one of:
	  ``{"status": "created",      "name": <lead-name>, "stage": "C0"}``
	  ``{"status": "existing",     "name": <lead-name>, "stage": <stage>}``
	  ``{"status": "closed_match",                       "stage": <terminal-stage>}``
	"""
	_verify_token()

	# Narrow required fields to non-empty `str` for the type-checker and so the
	# helpers below can pass them through without re-checking for None.
	name = (name or "").strip()
	mobile = (mobile or "").strip()
	if not name:
		frappe.throw(_("'name' is required."), exc=frappe.ValidationError)
	if not mobile:
		frappe.throw(_("'mobile' is required."), exc=frappe.ValidationError)

	e164, national_number = _normalize_phone(mobile)
	clean_email, email_note = _clean_email(email)

	# Warn (don't fail) if caller passed `lead_type` and it disagrees with the
	# value we derive from `customer_type`. The endpoint authoritatively derives
	# custom_lead_type from customer_type — passing lead_type is a no-op from a
	# legacy / drifted frontend that should be cleaned up.
	derived_lead_type = _derive_lead_type(customer_type)
	if lead_type and lead_type != derived_lead_type:
		frappe.log_error(
			title="Website Lead: lead_type override ignored",
			message=(
				f"Caller passed lead_type={lead_type!r}, but custom_lead_type is "
				f"derived as {derived_lead_type!r} from customer_type={customer_type!r}. "
				f"The passed lead_type value was ignored. Update the website frontend "
				f"to stop sending lead_type, or align it with the derivation rule."
			),
		)

	payload = {
		"message": message,
		"city": city,
		"project_type": project_type,
		"utm_source": utm_source,
		"utm_medium": utm_medium,
		"utm_campaign": utm_campaign,
		"utm_content": utm_content,
		"page_url": page_url,
	}
	extra_lines = [email_note] if email_note else []

	open_name, closed_stage = _find_existing_lead(e164, national_number, mobile)

	if open_name:
		# Intentionally do not overwrite stored email / pincode / UTM / etc.
		# on the existing lead — preserve first-touch attribution. The new
		# values are captured in the re-submission Comment for marketing.
		lead = cast("CRMLead", frappe.get_doc("CRM Lead", open_name))

		# Archived engagement is terminal even on an Open-typed C-stage.
		if lead.lead_status == "Archived":
			return {"status": "closed_match", "stage": lead.status}

		trail = [
			"[API_SUBMIT] Re-submission from indiframe.com contact form.",
			*_trail_parts(message, payload),
			*extra_lines,
		]

		# Auto-reactivate Cold engagement; Script 3 stamps custom_reactivated_at,
		# Script 1 enforces invariants. C-stage preserved per PRD §7.
		if lead.lead_status == "Cold-Unresponsive":
			lead.lead_status = "Reactivated"
			lead.save(ignore_permissions=True)
			lead.add_comment("Comment", "<br>".join(trail))
			return {"status": "reactivated", "name": open_name, "stage": lead.status}

		lead.add_comment("Comment", "<br>".join(trail))
		return {"status": "existing", "name": open_name, "stage": lead.status}

	if closed_stage:
		# Don't leak the closed lead's name; website should prompt the user
		# to call sales rather than silently re-submit.
		return {"status": "closed_match", "stage": closed_stage}

	first_name, last_name = _split_name(name)
	try:
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"last_name": last_name,
				"mobile_no": e164,
				"email": clean_email,
				"organization": (company or "").strip() or None,
				"status": DEFAULT_STATUS,
				"lead_status": DEFAULT_LEAD_STATUS,
				"source": WEBSITE_SOURCE,
				"custom_sub_source": WEBSITE_SUB_SOURCE,
				"custom_pincode": pincode or None,
				"custom_customer_type": customer_type or None,
				"custom_lead_type": derived_lead_type,
				"custom_utm_source": utm_source or None,
				"custom_utm_medium": utm_medium or None,
				"custom_utm_campaign": utm_campaign or None,
				"custom_utm_content": utm_content or None,
			}
		).insert(ignore_permissions=True)
	except frappe.ValidationError:
		# Possible race: a concurrent submission landed between our dedup
		# lookup and insert, and Phone Dedup (server script) rejected this
		# one. Re-check; if the racer is now visible as an Open lead, treat
		# this submission as that lead's re-submission. Otherwise the
		# original error stands.
		#
		# Why a broad ValidationError catch and not UniqueValidationError:
		# Phone Dedup throws via `frappe.throw(...)` which surfaces as plain
		# ValidationError, not UniqueValidationError (that subclass is for
		# DB-level UNIQUE-index violations, not server-script throws). On
		# unrelated validation failures (Select-option mismatch, etc.) the
		# re-lookup returns None and we re-raise — so callers still see the
		# real error, with at most one extra DB hit on the cold path.
		open_name, closed_stage = _find_existing_lead(e164, national_number, mobile)
		if open_name:
			lead = frappe.get_doc("CRM Lead", open_name)
			trail = [
				"[API_SUBMIT] Re-submission from indiframe.com contact form.",
				*_trail_parts(message, payload),
				*extra_lines,
			]
			lead.add_comment("Comment", "<br>".join(trail))
			return {"status": "existing", "name": open_name, "stage": lead.status}
		if closed_stage:
			return {"status": "closed_match", "stage": closed_stage}
		raise

	trail = [*_trail_parts(message, payload), *extra_lines]
	if trail:
		lead.add_comment("Comment", "<br>".join(["[API_SUBMIT] Captured from indiframe.com contact form.", *trail]))

	return {"status": "created", "name": lead.name, "stage": lead.status}

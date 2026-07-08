"""Public website lead-capture endpoint.

Exposed at: POST https://crm.indiframe.com/api/method/crm.api.website.create_lead

"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, cast

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import validate_email_address

from crm.utils import parse_phone_number, phone_dedup_candidates

if TYPE_CHECKING:
	from crm.fcrm.doctype.crm_lead.crm_lead import CRMLead

WEBSITE_SOURCE = "Direct"  # default source when the caller omits `source`
WEBSITE_SUB_SOURCE = "Website"
DEFAULT_STATUS = "C0"
DEFAULT_LEAD_STATUS = "Active"
TOKEN_HEADER = "X-IndiFrame-Token"
TOKEN_CONF_KEY = "indiframe_website_token"

PROJECTS_CUSTOMER_TYPES = frozenset({"Architect", "Builder", "Contractor"})

# This public/guest endpoint only ever legitimately originates Direct or Paid leads.
# Restricting (rather than validating against any CRM Lead Source) also keeps callers
# away from sources like "Referral"/"Lead Spotting" whose mandatory-sub_source rule
# (crm_lead.py's _SUB_SOURCE_TRIGGER_SOURCES) this endpoint has no way to satisfy.
ALLOWED_WEBSITE_SOURCES = frozenset({WEBSITE_SOURCE, "Paid"})


def _derive_lead_type(customer_type: str | None) -> str:
	return "Projects" if customer_type in PROJECTS_CUSTOMER_TYPES else "Retail"


def _resolve_source(source: str | None) -> str:
	"""Validate incoming source against ALLOWED_WEBSITE_SOURCES; fall back to default.

	Deliberately an allowlist, not "any existing CRM Lead Source" — see
	ALLOWED_WEBSITE_SOURCES for why. Public endpoint must not 500 over a bad
	source, so unknown/disallowed values default to WEBSITE_SOURCE and are logged.
	"""
	value = (source or "").strip()
	if value in ALLOWED_WEBSITE_SOURCES:
		return value
	if value:
		frappe.log_error(
			title="Website Lead: disallowed source",
			message=f"source={value!r} not permitted for this endpoint; defaulted to {WEBSITE_SOURCE!r}.",
		)
	return WEBSITE_SOURCE


def _resolve_sub_source(sub_source: str | None, resolved_source: str) -> str | None:
	"""Validate incoming sub_source against CRM Sub Source scoped to resolved_source.

	custom_sub_source is a Link field — an unknown value would fail insert
	validation. Public endpoint must not 500 over a bad sub_source, so
	unknown/blank values are dropped (logged) rather than raised.

	WEBSITE_SOURCE ("Direct") has a canonical fallback, WEBSITE_SUB_SOURCE
	("Website"). Other sources (e.g. "Paid") have no single generic
	sub_source to fall back to, so an unresolved value is left unset.
	"""
	value = (sub_source or "").strip()
	if not value:
		return WEBSITE_SUB_SOURCE if resolved_source == WEBSITE_SOURCE else None
	if frappe.db.exists("CRM Sub Source", {"name": value, "source": resolved_source}):
		return value
	frappe.log_error(
		title="Website Lead: unknown sub_source",
		message=f"sub_source={value!r} not found under source={resolved_source!r}.",
	)
	return WEBSITE_SUB_SOURCE if resolved_source == WEBSITE_SOURCE else None


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
	return phone_dedup_candidates(raw, e164, national_number)


def _find_existing_lead(e164: str, national_number: str, raw: str) -> tuple[str | None, str | None]:
	"""Phone lookup scoped to Open-type CRM Lead Statuses.

	Returns ``(open_match_name, closed_match_status)``. Exactly one of the
	two is non-None on a match; both are None on a fresh number.

	Closed leads (status type != "Open" — i.e. C4 Won, C6 Lost, Archived) deliberately
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
	if payload.get("sub_source"):
		bits.append(f"Sub Source: {payload['sub_source']}")
	if payload.get("city"):
		bits.append(f"City: {payload['city']}")
	if payload.get("state"):
		bits.append(f"State: {payload['state']}")
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


_LAST_TOUCH_FIELD_MAP = {
	"utm_last_touch_source": "custom_utm_last_touch_source",
	"utm_last_touch_medium": "custom_utm_last_touch_medium",
	"utm_last_touch_campaign": "custom_utm_last_touch_campaign",
	"utm_last_touch_content": "custom_utm_last_touch_content",
}


def _update_last_touch(lead, last_touch: dict) -> bool:
	"""Set any provided (non-empty) last-touch UTM value on lead; return whether anything changed.

	Omitted/blank values are left as-is — never null out a previously-stored
	last-touch value just because this particular resubmission didn't carry it
	(e.g. an older indiframe-web client mid-rollout).
	"""
	changed = False
	for param_key, field_name in _LAST_TOUCH_FIELD_MAP.items():
		value = last_touch.get(param_key)
		if value:
			lead.set(field_name, value)
			changed = True
	return changed


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
	state: str | None = None,
	company: str | None = None,
	message: str | None = None,
	customer_type: str | None = None,
	source: str | None = None,
	sub_source: str | None = None,
	lead_type: str | None = None,  # ignored; custom_lead_type is derived from customer_type
	project_type: str | None = None,
	utm_source: str | None = None,
	utm_medium: str | None = None,
	utm_campaign: str | None = None,
	utm_content: str | None = None,
	utm_last_touch_source: str | None = None,
	utm_last_touch_medium: str | None = None,
	utm_last_touch_campaign: str | None = None,
	utm_last_touch_content: str | None = None,
	utm_first_touch_source: str | None = None,
	utm_first_touch_medium: str | None = None,
	utm_first_touch_campaign: str | None = None,
	utm_first_touch_content: str | None = None,
	page_url: str | None = None,
) -> dict:
	"""Create or re-attribute a CRM Lead from an indiframe.com form submission.

	The website (indiframe-web) is responsible for sending canonical snake_case
	keys and properly-cased Select values (`Homeowner` not `homeowner`, etc.).
	This endpoint does not perform alias / case normalization.

	``utm_source``/``utm_medium``/``utm_campaign``/``utm_content`` are frozen at
	first conversion attempt: captured on creation, never overwritten on
	resubmission (see custom_utm_section's description) — NOT necessarily the
	visitor's literal first-ever ad click, since it's whatever page/session the
	lead happened to be created from. ``utm_first_touch_*`` is the true
	first-touch counterpart: the client's genuinely first-captured attribution
	(persisted 180 days), also write-once on creation, never overwritten.
	``utm_last_touch_*`` is the last-touch set: stored on creation same as the
	others, but updated on every resubmission where a value is provided (an
	omitted one — e.g. an older client mid-rollout — is left as-is, never
	nulled out).

	Returns one of:
	  ``{"status": "created",      "name": <lead-name>, "stage": "C0"}``
	  ``{"status": "existing",     "name": <lead-name>, "stage": <stage>}``
	  ``{"status": "reactivated",  "name": <lead-name>, "stage": <stage>}``
	  ``{"status": "closed_match",                       "stage": <terminal-stage>}``

	``reactivated`` fires when the matched lead was at ``lead_status =
	Cold-Unresponsive`` and got auto-flipped back to ``Reactivated`` by this
	endpoint. The website should treat it like ``existing`` for display
	purposes; marketing can mine the signal separately.
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

	resolved_source = _resolve_source(source)
	resolved_sub_source = _resolve_sub_source(sub_source, resolved_source)

	payload = {
		"message": message,
		"sub_source": sub_source,
		"city": city,
		"state": state,
		"project_type": project_type,
		"utm_source": utm_source,
		"utm_medium": utm_medium,
		"utm_campaign": utm_campaign,
		"utm_content": utm_content,
		"page_url": page_url,
	}
	last_touch = {
		"utm_last_touch_source": utm_last_touch_source,
		"utm_last_touch_medium": utm_last_touch_medium,
		"utm_last_touch_campaign": utm_last_touch_campaign,
		"utm_last_touch_content": utm_last_touch_content,
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
		last_touch_changed = _update_last_touch(lead, last_touch)

		# Auto-reactivate Cold engagement; Script 3 stamps custom_reactivated_at,
		# Script 1 enforces invariants. C-stage preserved per PRD §7.
		if lead.lead_status == "Cold-Unresponsive":
			lead.lead_status = "Reactivated"
			# Guest has no ownership/role standing; this narrow flag lets the
			# already-allowed Cold-Unresponsive -> Reactivated move through
			# without tripping _apply_stage_transition_guard's ownership check.
			lead.flags.ignore_stage_transition_guard = True
			lead.save(ignore_permissions=True)
			lead.add_comment("Comment", "<br>".join(trail))
			return {"status": "reactivated", "name": open_name, "stage": lead.status}

		if last_touch_changed:
			lead.save(ignore_permissions=True)
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
				"source": resolved_source,
				"custom_sub_source": resolved_sub_source,
				"custom_pincode": pincode or None,
				"custom_city": city or None,
				"custom_state": state or None,
				"custom_customer_type": customer_type or None,
				"custom_lead_type": derived_lead_type,
				"custom_utm_source": utm_source or None,
				"custom_utm_medium": utm_medium or None,
				"custom_utm_campaign": utm_campaign or None,
				"custom_utm_content": utm_content or None,
				"custom_utm_last_touch_source": utm_last_touch_source or None,
				"custom_utm_last_touch_medium": utm_last_touch_medium or None,
				"custom_utm_last_touch_campaign": utm_last_touch_campaign or None,
				"custom_utm_last_touch_content": utm_last_touch_content or None,
				"custom_utm_first_touch_source": utm_first_touch_source or None,
				"custom_utm_first_touch_medium": utm_first_touch_medium or None,
				"custom_utm_first_touch_campaign": utm_first_touch_campaign or None,
				"custom_utm_first_touch_content": utm_first_touch_content or None,
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
			if _update_last_touch(lead, last_touch):
				lead.save(ignore_permissions=True)
			lead.add_comment("Comment", "<br>".join(trail))
			return {"status": "existing", "name": open_name, "stage": lead.status}
		if closed_stage:
			return {"status": "closed_match", "stage": closed_stage}
		raise

	trail = [*_trail_parts(message, payload), *extra_lines]
	if trail:
		lead.add_comment(
			"Comment", "<br>".join(["[API_SUBMIT] Captured from indiframe.com contact form.", *trail])
		)

	return {"status": "created", "name": lead.name, "stage": lead.status}

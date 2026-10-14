"""Meta Custom Audience (Customer List) sync for CRM Lead / CRM Deal.

Architecture
------------
- One audience, one direction of truth per doctype event: a new ``CRM Lead``
  adds the person to the audience; a ``CRM Deal`` moving to a status whose
  ``type`` is ``Won`` removes them — so converted leads stop seeing ads.
- Credentials (access token, audience id, API version) live in the single
  ``CRM Meta Audience Settings`` doctype, never hard-coded or in site_config.
  The default phone country code and default country are also configurable
  there (not hard-coded), since both are currently India-only assumptions
  that may need to change later.
- All outbound calls run through ``frappe.enqueue`` (see ``hooks.py``'s
  ``after_insert``/``on_update`` wiring) so a slow/unavailable Meta API never
  blocks a doc save.
- Every value sent to Meta is normalized (trim, lowercase, digits-only phone
  with country code) and SHA-256 hashed before leaving this process — Meta's
  API only ever sees hashes, never raw PII. A value that is genuinely blank
  (no email, no phone, etc.) is sent as the hash of "" per Meta's own
  "blank = empty string" rule — but a *row* with every field blank is
  pointless noise and should never be sent at all (see handlers.py/backfill.py).
- A failed call is logged to ``Failed Meta Sync Log`` with enough context to
  retry by hand; it never raises back into the doc-save transaction.
"""

from __future__ import annotations

import hashlib
import re

import frappe
import requests

from crm.lead_syncing.doctype.lead_sync_source.facebook import FB_GRAPH_API_BASE, get_fb_graph_api_url

AUDIENCE_SCHEMA = ["EMAIL", "PHONE", "FN", "LN", "COUNTRY"]

GRAPH_API_BASE = FB_GRAPH_API_BASE
DEFAULT_GRAPH_API_VERSION = "v23.0"

# Emergency fallbacks only — the real, configurable defaults live on
# CRM Meta Audience Settings (default_phone_country_code / default_country)
# so they can be changed without a code deploy once leads start coming in
# from outside India.
_FALLBACK_PHONE_COUNTRY_CODE = "91"
_FALLBACK_COUNTRY = "in"

REQUEST_TIMEOUT = 10


def _settings_value(fieldname: str, fallback: str) -> str:
	value = frappe.db.get_single_value("CRM Meta Audience Settings", fieldname)
	return value or fallback


def _default_phone_country_code() -> str:
	return _settings_value("default_phone_country_code", _FALLBACK_PHONE_COUNTRY_CODE)


def _default_country() -> str:
	return _settings_value("default_country", _FALLBACK_COUNTRY)


def _normalize_text(value: str | None) -> str:
	"""Trim + lowercase. Empty/None becomes "" (Meta's own "blank = empty string" rule)."""
	if not value:
		return ""
	return value.strip().lower()


def _normalize_phone(value: str | None) -> str:
	"""Digits-only, with a country code. Empty/None becomes "".

	Strips everything but digits (including any leading zero a local number
	may have been dialled/entered with, e.g. "09876543210"), then prefixes
	the configured default country code unless the number is already long
	enough to plausibly include one.
	"""
	if not value:
		return ""
	digits = re.sub(r"\D", "", value)
	digits = digits.lstrip("0")
	if not digits:
		return ""
	# A bare 10-digit Indian mobile number has no country code yet; anything
	# longer is assumed to already carry one (e.g. 91XXXXXXXXXX).
	if len(digits) == 10:
		digits = _default_phone_country_code() + digits
	return digits


def _sha256(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_field(value: str | None, *, is_phone: bool = False) -> str:
	"""Normalize then SHA-256-hex a single field.

	A genuinely blank/normalized-to-empty value is sent as a literal "" —
	never hashed. Hashing "" would produce a real, specific SHA-256 digest
	that Meta has no way to distinguish from actual (coincidentally-blank)
	data; "" is Meta's own documented convention for "no value for this
	schema column". The column is still present and positional — just with
	an empty string, not a hash of one.
	"""
	normalized = _normalize_phone(value) if is_phone else _normalize_text(value)
	if not normalized:
		return ""
	return _sha256(normalized)


def has_matchable_contact(*, email: str | None, phone: str | None) -> bool:
	"""True if there's at least an email or a phone to match a person on.

	Checked before building/sending a row — not after hashing — because the
	country default (see build_user_row) means a built row is never fully
	blank even when the actual person info (email/phone/name) is. A row with
	no email and no phone is unmatchable noise regardless of what else is in
	it, and should never be sent."""
	return bool(email) or bool(phone)


def build_user_row(
	*,
	email: str | None,
	phone: str | None,
	first_name: str | None,
	last_name: str | None,
	country: str | None,
) -> list[str]:
	"""Build one AUDIENCE_SCHEMA-ordered, hashed row for the API payload.

	``country`` falls back to the configured default country (CRM doesn't
	have a country field on Lead/Deal today) rather than always being sent
	blank — but still respects an explicit "" from a caller that really
	wants it blank.
	"""
	resolved_country = country if country is not None else _default_country()
	return [
		hash_field(email),
		hash_field(phone, is_phone=True),
		hash_field(first_name),
		hash_field(last_name),
		hash_field(resolved_country),
	]


def build_payload(rows: list[list[str]]) -> dict:
	return {"payload": {"schema": AUDIENCE_SCHEMA, "data": rows}}


def get_settings() -> frappe.model.document.Document | None:
	"""Return the enabled CRM Meta Audience Settings doc, or None if disabled/unset."""
	settings = frappe.get_cached_doc("CRM Meta Audience Settings")
	if (
		not settings.enabled
		or not settings.get_password("access_token", raise_exception=False)
		or not settings.audience_id
	):
		return None
	return settings


class MetaAudienceNotConfigured(frappe.ValidationError):
	pass


def _audience_users_url(settings) -> str:
	version = settings.graph_api_version or DEFAULT_GRAPH_API_VERSION
	return get_fb_graph_api_url(f"/{settings.audience_id}/users", version=version)


def _call(method: str, settings, rows: list[list[str]]) -> dict:
	"""Call the audience /users endpoint directly via ``requests`` rather than
	Frappe's make_post_request/make_delete_request wrappers — those don't
	accept a ``timeout`` kwarg, and sending the token as an ``access_token``
	query param (the pre-existing facebook.py pattern) means it ends up in
	raise_for_status's error message, and from there into Error Log /
	Failed Meta Sync Log.traceback. Using an auth header keeps the token out
	of the URL (and therefore out of any logged exception message/traceback),
	and a timeout stops a hung Meta API call from blocking the worker forever.
	"""
	url = _audience_users_url(settings)
	payload = build_payload(rows)
	token = settings.get_password("access_token")
	headers = {"Authorization": "Bearer " + token}
	response = requests.request(method, url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
	response.raise_for_status()
	return response.json()


def add_users_to_audience(rows: list[list[str]]) -> dict:
	"""POST hashed rows to the configured audience.

	Raises MetaAudienceNotConfigured if sync is disabled/incomplete, and
	raises the underlying HTTP error on a failed call — callers (handlers,
	backfill, retry_sync) are expected to catch and log/retry, never to
	treat a swallowed exception as a silent success.
	"""
	settings = get_settings()
	if settings is None:
		raise MetaAudienceNotConfigured(
			frappe._("Meta Audience sync is disabled or incomplete — nothing was sent.")
		)
	return _call("POST", settings, rows)


def remove_users_from_audience(rows: list[list[str]]) -> dict:
	"""DELETE hashed rows from the configured audience. See add_users_to_audience for error semantics."""
	settings = get_settings()
	if settings is None:
		raise MetaAudienceNotConfigured(
			frappe._("Meta Audience sync is disabled or incomplete — nothing was sent.")
		)
	return _call("DELETE", settings, rows)


def log_sync_failure(sync_type: str, entity: str, entity_name: str, payload_row: list[str]) -> None:
	"""Record a failed Add/Remove call to ``Failed Meta Sync Log`` for manual retry.

	Shared by handlers.py (per-record doc-event failures) and backfill.py
	(per-lead batch failures) so there's exactly one place a failure gets
	written — never both a Failed Meta Sync Log row and a separate
	frappe.log_error for the same failure (the log row already carries the
	traceback, so a second Error Log entry is just noise).
	"""
	try:
		frappe.get_doc(
			{
				"doctype": "Failed Meta Sync Log",
				"sync_type": sync_type,
				"entity": entity,
				"entity_name": entity_name,
				"payload_row": frappe.as_json(payload_row),
				"traceback": frappe.get_traceback(),
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Logging the failure must never itself raise and swallow the original
		# error context — fall back to the generic error log only here, since
		# this is the one case where we genuinely couldn't record it anywhere else.
		frappe.log_error(title=f"Failed to log Meta Audience sync failure ({sync_type}/{entity_name})")

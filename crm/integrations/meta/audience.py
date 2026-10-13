"""Meta Custom Audience (Customer List) sync for CRM Lead / CRM Deal.

Architecture
------------
- One audience, one direction of truth per doctype event: a new ``CRM Lead``
  adds the person to the audience; a ``CRM Deal`` moving to a status whose
  ``type`` is ``Won`` removes them — so converted leads stop seeing ads.
- Credentials (access token, audience id, API version) live in the single
  ``CRM Meta Audience Settings`` doctype, never hard-coded or in site_config.
- All outbound calls run through ``frappe.enqueue`` (see ``hooks.py``'s
  ``after_insert``/``on_update`` wiring) so a slow/unavailable Meta API never
  blocks a doc save.
- Every value sent to Meta is normalized (trim, lowercase, digits-only phone
  with country code) and SHA-256 hashed before leaving this process — Meta's
  API only ever sees hashes, never raw PII.
- A failed call is logged to ``Failed Meta Sync Log`` with enough context to
  retry by hand; it never raises back into the doc-save transaction.
"""

from __future__ import annotations

import hashlib
import re

import frappe
from frappe.integrations.utils import make_delete_request, make_post_request

AUDIENCE_SCHEMA = ["EMAIL", "PHONE", "FN", "LN", "COUNTRY"]

GRAPH_API_BASE = "https://graph.facebook.com"
DEFAULT_GRAPH_API_VERSION = "v23.0"

# Default country-code prefix applied to phone numbers that don't already
# start with one. India-only today; revisit if leads start coming in from
# other countries (see developer brief Q&A).
DEFAULT_PHONE_COUNTRY_CODE = "91"


def _normalize_text(value: str | None) -> str:
	"""Trim + lowercase. Empty/None becomes "" (Meta's own "blank = empty string" rule)."""
	if not value:
		return ""
	return value.strip().lower()


def _normalize_phone(value: str | None) -> str:
	"""Digits-only, with a country code. Empty/None becomes "".

	Strips everything but digits, then prefixes DEFAULT_PHONE_COUNTRY_CODE
	unless the number is already long enough to plausibly include one.
	"""
	if not value:
		return ""
	digits = re.sub(r"\D", "", value)
	if not digits:
		return ""
	# A bare 10-digit Indian mobile number has no country code yet; anything
	# longer is assumed to already carry one (e.g. 91XXXXXXXXXX).
	if len(digits) == 10:
		digits = DEFAULT_PHONE_COUNTRY_CODE + digits
	return digits


def _sha256(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_field(value: str | None, *, is_phone: bool = False) -> str:
	"""Normalize then SHA-256-hex a single field. Always returns a hash, even of "" ."""
	normalized = _normalize_phone(value) if is_phone else _normalize_text(value)
	return _sha256(normalized)


def build_user_row(
	*,
	email: str | None,
	phone: str | None,
	first_name: str | None,
	last_name: str | None,
	country: str | None,
) -> list[str]:
	"""Build one AUDIENCE_SCHEMA-ordered, hashed row for the API payload."""
	return [
		hash_field(email),
		hash_field(phone, is_phone=True),
		hash_field(first_name),
		hash_field(last_name),
		hash_field(country),
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


def _audience_users_url(settings) -> str:
	version = settings.graph_api_version or DEFAULT_GRAPH_API_VERSION
	return f"{GRAPH_API_BASE}/{version}/{settings.audience_id}/users"


def _call(method: str, settings, rows: list[list[str]]) -> dict:
	url = _audience_users_url(settings)
	payload = build_payload(rows)
	params = {"access_token": settings.get_password("access_token")}
	if method == "POST":
		return make_post_request(url, params=params, json=payload)
	return make_delete_request(url, params=params, json=payload)


def add_users_to_audience(rows: list[list[str]]) -> dict:
	"""POST hashed rows to the configured audience. Raises on failure — caller logs/retries."""
	settings = get_settings()
	if settings is None:
		frappe.logger("meta_audience").info("Meta Audience sync skipped: settings disabled/incomplete")
		return {}
	return _call("POST", settings, rows)


def remove_users_from_audience(rows: list[list[str]]) -> dict:
	"""DELETE hashed rows from the configured audience. Raises on failure — caller logs/retries."""
	settings = get_settings()
	if settings is None:
		frappe.logger("meta_audience").info("Meta Audience sync skipped: settings disabled/incomplete")
		return {}
	return _call("DELETE", settings, rows)

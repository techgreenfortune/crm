"""HMAC-signed file download for external integrations.

Used by the project API handoff to give OpsGate a short-lived, unauthenticated
URL pointing at a private quote PDF. The signature is computed over
``(file_doc_name, expires_at)`` using ``crm_sso_secret`` from site_config.json
— the same secret CRM already shares with OpsGate for the SSO flow. OpsGate
does not verify the signature; it just GETs the URL. Verification happens here
when the URL is fetched.

The token references a File doc by name, never a raw path, so path-traversal
attacks against private storage are not possible — `get_file_path` only
resolves a known File doc to its on-disk location.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import frappe
from frappe import _
from frappe.utils import get_url
from frappe.utils.file_manager import get_file_path

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h — enough for OpsGate to download and copy to S3


def _signing_secret() -> str:
	secret = frappe.conf.get("crm_sso_secret")
	if not secret:
		frappe.throw(
			_("CRM SSO secret is not configured. Add crm_sso_secret to site_config.json"),
			frappe.AuthenticationError,
		)
	return secret


def _sign(file_doc_name: str, expires_at: int) -> str:
	return hmac.new(
		_signing_secret().encode(),
		f"{file_doc_name}|{expires_at}".encode(),
		hashlib.sha256,
	).hexdigest()


def build_signed_file_url(file_url: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str | None:
	"""Return a full https URL that any client can GET to download the file.

	``file_url`` is whatever Frappe's Attach field stores (e.g. ``/private/files/xyz.pdf``
	or ``/files/abc.pdf``). We resolve it to a File doc name so the token is
	traversal-safe, then sign ``(file_doc_name, expires_at)``.

	Returns None if the file_url cannot be resolved to a File doc — caller
	should treat that as "no file to share".
	"""
	if not file_url:
		return None

	file_doc_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_doc_name:
		return None

	expires_at = int(time.time()) + ttl_seconds
	sig = _sign(file_doc_name, expires_at)
	base = get_url().rstrip("/")
	return f"{base}/api/method/crm.api.files.get_signed_file?fid={file_doc_name}&exp={expires_at}&sig={sig}"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_signed_file():
	"""Serve a private File doc to an unauthenticated caller if the HMAC checks out.

	Auth is the signature — there is no Frappe session. The link is single-use
	in spirit (TTL-bound) but not enforced as such; OpsGate is expected to
	download immediately and copy to its own S3.
	"""
	fid = frappe.form_dict.get("fid") or ""
	exp = frappe.form_dict.get("exp") or ""
	sig = frappe.form_dict.get("sig") or ""

	if not (fid and exp and sig):
		frappe.local.response.http_status_code = 400
		frappe.throw(_("Missing fid/exp/sig"), frappe.AuthenticationError)

	try:
		expires_at = int(exp)
	except ValueError:
		frappe.local.response.http_status_code = 400
		frappe.throw(_("Invalid expiry"), frappe.AuthenticationError)

	if time.time() > expires_at:
		frappe.local.response.http_status_code = 410
		frappe.throw(_("Link expired"), frappe.AuthenticationError)

	expected = _sign(fid, expires_at)
	if not hmac.compare_digest(sig, expected):
		frappe.local.response.http_status_code = 401
		frappe.throw(_("Invalid signature"), frappe.AuthenticationError)

	# get_file_path resolves the File doc name → on-disk path (handles
	# /private/files/ and /files/ alike) and refuses paths containing "../".
	disk_path = get_file_path(fid)
	if not disk_path or not os.path.exists(disk_path):
		frappe.local.response.http_status_code = 404
		frappe.throw(_("File not found"), frappe.DoesNotExistError)

	with open(disk_path, "rb") as f:
		filecontent = f.read()

	frappe.local.response.filename = os.path.basename(disk_path)
	frappe.local.response.filecontent = filecontent
	frappe.local.response.type = "download"

from urllib.parse import urlsplit

import frappe
from frappe import _


def get_s3_recording_location(url):
	bucket = frappe.conf.get("crm_recording_bucket")
	prefix = frappe.conf.get("crm_recording_prefix")
	if not isinstance(bucket, str) or not bucket or not isinstance(prefix, str) or not prefix.strip("/"):
		frappe.throw(
			_("Configure crm_recording_bucket and crm_recording_prefix before using S3 recordings."),
			frappe.ValidationError,
		)
	prefix = prefix.strip("/")
	parts = urlsplit(url)
	key = parts.path.removeprefix("/")
	if (
		parts.scheme != "s3"
		or parts.netloc != bucket
		or parts.query
		or parts.fragment
		or not key.startswith(prefix + "/")
		or any(part in ("", ".", "..") for part in key.split("/"))
		or "\\" in key
		or any(ord(char) < 32 for char in key)
	):
		frappe.throw(_("Recording location is outside configured S3 storage."), frappe.PermissionError)
	return bucket, key


def validate_recording_url_change(doc):
	old = doc.get_doc_before_save()
	previous = old.recording_url if old else ""
	current = doc.recording_url or ""
	if previous == current or not any(value.startswith("s3://") for value in (previous or "", current)):
		return
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can change S3 recording locations."), frappe.PermissionError)
	if current.startswith("s3://"):
		get_s3_recording_location(current)

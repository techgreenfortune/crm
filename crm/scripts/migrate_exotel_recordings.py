"""Copy old Exotel recordings to private S3 storage and link historical calls.

Run in a DEDICATED bench execute process, never inside a console transaction:

    bench --site crm.indiframe.com execute crm.scripts.migrate_exotel_recordings.run \
        --kwargs '{"account_sid":"...","api_key":"...","api_token":"...",\
        "date_from":"2026-06-01","date_to":"2026-09-30","bucket":"...",\
        "region":"ap-south-1","dry_run":1,"limit":5}'

dry_run=1 reads Exotel, CRM and S3 metadata only; it prints each planned action
and writes a local CSV. It does NOT download audio or validate actual playback.
limit counts ALL scanned calls, including calls without recordings.
Set dry_run=0 to apply the same plan. Each successful call commits independently.
The CSV is flushed and fsynced after each row, including skipped/error rows.
New Call Logs use Exotel DateCreated as their timeline date; existing logs keep
their original creation timestamp.

Before migration, configure the allowed location on the site:

    bench --site crm.indiframe.com set-config crm_recording_bucket YOUR_BUCKET
    bench --site crm.indiframe.com set-config crm_recording_prefix exotel-old-recordings
    bench --site crm.indiframe.com set-config crm_recording_region ap-south-1

AWS credentials use boto3's standard chain. Both the migration process and CRM
backend need S3 access; playback generates short-lived URLs, never stores them.
bucket/prefix arguments must remain inside the configured recording location.
region defaults to crm_recording_region, then AWS_DEFAULT_REGION.
Ordinary users cannot assign, replace or remove S3 recording locations.
The existing phone matcher prefers deals and excludes
converted leads; the printed references are authoritative, not necessarily leads.
Existing S3 objects are reused only when their migration metadata is verified.
Downloads are staged on local disk and length-checked BEFORE uploading.
Presigned URL fields take precedence over legacy RecordingUrl. Downloads start
without credentials; only unsigned recordings.exotel.com URLs returning 401 may
be retried with the explicitly supplied old-account credentials.
Exotel API contract: https://developer.exotel.com/docs/voice-v1/api-reference/call-details
Daily requests stay within its maximum one-month query range.
"""

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import frappe
import requests
from frappe.utils import cint, cstr, get_datetime, get_url
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from crm.integrations.api import get_contact_by_phone_number
from crm.integrations.exotel.handler import get_calls_api_call_log_status
from crm.permissions.recordings import get_s3_recording_location

TIMEOUT = (10, 60)
TERMINAL_STATUSES = {"completed", "no-answer", "busy", "failed", "canceled"}
RECORDING_URL_FIELDS = ("PreSignedRecordingUrl", "pre_signed_recording_url", "RecordingUrl", "recording_url")
REPORT_FIELDS = [
	"call_sid",
	"source_field",
	"phone",
	"references",
	"call_log",
	"previous_url",
	"s3_url",
	"status",
	"duration",
	"call_time",
	"upload_action",
	"log_action",
	"attachment_action",
	"result",
	"detail",
]


def run(
	account_sid,
	api_key,
	api_token,
	date_from,
	date_to,
	bucket,
	subdomain="api.exotel.com",
	prefix="exotel-old-recordings",
	region=None,
	dry_run=1,
	limit=0,
	report_path="/tmp/exotel_recording_migration.csv",
):
	import boto3

	if dry_run not in (0, 1, False, True, "0", "1"):
		raise ValueError("dry_run must be 0 or 1")
	dry_run, limit = bool(cint(dry_run)), int(limit)
	if limit < 0:
		raise ValueError("limit must be nonnegative")
	start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
	if start > end:
		raise ValueError("date_from must not be after date_to")
	if not re.fullmatch(r"[A-Za-z0-9_-]+", account_sid):
		raise ValueError("Invalid account_sid")
	if subdomain not in ("api.exotel.com", "api.in.exotel.com"):
		raise ValueError("Unsupported Exotel subdomain")
	if not bucket or "/" in bucket:
		raise ValueError("Invalid S3 bucket")
	prefix = prefix.strip("/")
	get_s3_recording_location(f"s3://{bucket}/{prefix}/{account_sid}/recording.mp3")
	end_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time())
	seen, counts = set(), Counter()
	summary = {"dry_run": dry_run, "report": os.path.abspath(report_path)}
	s3 = boto3.client("s3", region_name=region or frappe.conf.get("crm_recording_region"))
	os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)

	# report_path is operator-selected in this non-whitelisted bench command, not HTTP input.
	with (
		_session() as session,
		open(  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-security-file-traversal
			report_path, "w", newline=""
		) as report,
	):
		session.auth = (api_key, api_token)
		writer = csv.DictWriter(report, fieldnames=REPORT_FIELDS)
		writer.writeheader()
		report.flush()
		os.fsync(report.fileno())
		try:
			for call in _iter_calls(session, subdomain, account_sid, start, end_exclusive):
				sid = _pick(call, "Sid", "sid", "CallSid", "call_sid")
				if sid and sid in seen:
					continue
				if sid:
					seen.add(sid)
				row = _process_call(
					call, s3, bucket, prefix, account_sid, dry_run, session=session, subdomain=subdomain
				)
				writer.writerow(row)
				report.flush()
				os.fsync(report.fileno())
				print(json.dumps(row), flush=True)
				counts[row["result"]] += 1
				if limit and sum(counts.values()) >= limit:
					break
		except Exception:
			summary["aborted"] = True
			raise
		finally:
			summary.update(total=sum(counts.values()), counts=dict(counts))
			print(json.dumps(summary), flush=True)
	return summary


def _session():
	session = requests.Session()
	retry = Retry(
		total=4,
		backoff_factor=1,
		status_forcelist=[429, 500, 502, 503, 504],
		allowed_methods=["GET"],
		respect_retry_after_header=True,
	)
	session.mount("https://", HTTPAdapter(max_retries=retry))
	return session


def _process_call(
	call, s3, bucket, prefix, account_sid, dry_run, *, session=None, subdomain="api.exotel.com"
):
	row = dict.fromkeys(REPORT_FIELDS, "")
	row["call_sid"] = _pick(call, "Sid", "sid", "CallSid", "call_sid") or ""
	savepoint = False
	try:
		sid = row["call_sid"]
		if not re.fullmatch(r"[A-Za-z0-9_-]{1,140}", sid):
			raise ValueError("Missing or invalid CallSid")
		source_url = _pick(call, *RECORDING_URL_FIELDS)
		if not source_url:
			row["result"], row["detail"] = "skipped", "No recording URL"
			return row
		row["source_field"] = next(field for field in RECORDING_URL_FIELDS if call.get(field) == source_url)
		_validate_recording_url(source_url)
		call = _with_call_details(call, session, subdomain, account_sid)
		key = "/".join(part for part in (prefix, account_sid, f"{sid}.mp3") if part)
		row["s3_url"] = f"s3://{bucket}/{key}"
		get_s3_recording_location(row["s3_url"])
		row["status"], row["duration"] = _status(call), _duration(call)
		row["call_time"] = cstr(get_datetime(call["DateCreated"]))
		row["phone"] = _customer_number(call)
		log, references = _resolve_call(call, sid, row["phone"], row["s3_url"])
		row["call_log"] = log.name if log else sid
		row["references"] = json.dumps(references)
		row["previous_url"] = _redact_url(log.recording_url) if log else ""
		row["log_action"] = (
			"unchanged" if log and log.recording_url == row["s3_url"] else "update" if log else "create"
		)
		row["upload_action"] = "reuse" if _s3_has(s3, bucket, key, account_sid, sid) else "upload"
		missing_attachments = [
			(doctype, docname)
			for doctype, docname in references
			if doctype in ("CRM Lead", "CRM Deal")
			and not _attachment_exists(doctype, docname, row["call_log"])
		]
		row["attachment_action"] = f"create:{len(missing_attachments)}"
		if not references:
			raise ValueError("No matching lead/deal/contact; no upload or CRM writes performed")
		if dry_run:
			row["result"] = "planned"
			return row

		frappe.db.savepoint("exotel_recording_call")
		savepoint = True
		if row["upload_action"] == "upload":
			_upload(
				source_url,
				s3,
				bucket,
				key,
				account_sid,
				sid,
				recording_auth=session.auth if session is not None else None,
			)
		_upsert_call_log(call, sid, row["s3_url"], log, references)
		for doctype, docname in missing_attachments:
			_attach_to_reference(doctype, docname, row["call_log"], sid)
		frappe.db.commit()
		savepoint = False
		row["result"] = "saved"
	except Exception as exc:
		if savepoint:
			frappe.db.rollback(save_point="exotel_recording_call")
		row["result"] = "error"
		# HTTP exceptions can embed expiring signed URLs; keep them out of persistent reports.
		row["detail"] = (
			f"{type(exc).__name__}: recording/API request failed"
			if isinstance(exc, requests.RequestException)
			else str(exc)
		)
	return row


def _iter_calls(session, subdomain, account_sid, start, end_exclusive):
	window_start = datetime.combine(start, datetime.min.time())
	while window_start < end_exclusive:
		window_end = min(window_start + timedelta(days=1), end_exclusive)
		yield from _iter_window(session, subdomain, account_sid, window_start, window_end)
		window_start = window_end


def _iter_window(session, subdomain, account_sid, start, end_exclusive):
	url = f"https://{subdomain}/v1/Accounts/{account_sid}/Calls.json"
	# Inclusive next-midnight upper bound avoids fractional-second gaps. Filter that
	# final boundary locally instead of assuming Exotel supports the `lt` operator.
	params = {
		"DateCreated": f"gte:{start:%Y-%m-%d %H:%M:%S};lte:{end_exclusive:%Y-%m-%d %H:%M:%S}",
		"PageSize": 100,
		"SortBy": "DateCreated:asc",
		"RecordingUrlValidity": 60,
	}
	pages = set()
	while url:
		page = (url, urlencode(params or {}))
		if page in pages:
			raise ValueError("Exotel returned a repeated pagination URL")
		pages.add(page)
		response = session.get(url, params=params, timeout=TIMEOUT)
		response.raise_for_status()
		body = response.json()
		for call in _extract_calls(body):
			created = _pick(call, "DateCreated", "date_created")
			if not created:
				raise ValueError("Call missing DateCreated; cannot verify requested date range")
			created = get_datetime(created)
			if created.tzinfo is not None:
				raise ValueError("Timezone-aware DateCreated requires explicit Exotel timezone handling")
			if start <= created < end_exclusive:
				yield call
		url, params = _next_url(body, subdomain), None


def _extract_calls(body):
	if not isinstance(body, dict):
		raise ValueError("Unexpected Exotel response: expected Calls envelope")
	envelope = body.get("RestResponse", body)
	if not isinstance(envelope, dict) or not isinstance(envelope.get("Calls"), list):
		raise ValueError("Unexpected Exotel response: Calls list missing")
	if not all(isinstance(call, dict) for call in envelope["Calls"]):
		raise ValueError("Unexpected Exotel call entry")
	return envelope["Calls"]


def _next_url(body, subdomain):
	envelope = body.get("RestResponse", body)
	meta = envelope.get("Metadata") or envelope.get("metadata") or body.get("Metadata") or {}
	nxt = _pick(meta, "NextPageUri", "next_page_uri")
	if not nxt:
		return None
	url = urljoin(f"https://{subdomain}/", nxt)
	parts = urlsplit(url)
	if parts.scheme != "https" or parts.netloc != subdomain:
		raise ValueError("Exotel pagination URL must stay on configured HTTPS host")
	params = dict(parse_qsl(parts.query))
	params.setdefault("RecordingUrlValidity", "60")
	return urlunsplit(parts._replace(query=urlencode(params)))


def _pick(data, *keys):
	for key in keys:
		value = data.get(key)
		if value is not None and cstr(value).strip().lower() not in ("", "null", "none"):
			return value
	return None


def _is_inbound(call):
	direction = cstr(_pick(call, "Direction", "direction")).lower()
	if direction.startswith("inbound") or direction == "incoming":
		return True
	if direction.startswith("outbound") or direction == "outgoing":
		return False
	raise ValueError(f"Unsupported Exotel direction: {direction!r}")


def _customer_number(call):
	number = _pick(call, "From", "from") if _is_inbound(call) else _pick(call, "To", "to")
	if not number:
		raise ValueError("Call missing customer phone number")
	return cstr(number)


def _status(call):
	status = cstr(_pick(call, "Status", "status")).lower()
	if status not in TERMINAL_STATUSES:
		raise ValueError(f"Unsupported historical call status: {status!r}")
	_duration(call)
	return get_calls_api_call_log_status(call)


def _duration(call):
	details = call.get("Details")
	if not isinstance(details, dict) or details.get("ConversationDuration") is None:
		raise ValueError("Call details missing ConversationDuration")
	if not all(key in details for key in ("Leg1Status", "Leg2Status")):
		raise ValueError("Call details missing leg statuses")
	duration = float(details["ConversationDuration"])
	if not math.isfinite(duration) or duration < 0:
		raise ValueError("Invalid ConversationDuration")
	return duration


def _with_call_details(call, session, subdomain, account_sid):
	details = call.get("Details")
	if (
		isinstance(details, dict)
		and details.get("ConversationDuration") is not None
		and all(key in details for key in ("Leg1Status", "Leg2Status"))
	):
		return call
	sid = _pick(call, "Sid", "sid", "CallSid", "call_sid")
	if session is None:
		raise ValueError("Old-account API session required to fetch call details")
	response = session.get(
		f"https://{subdomain}/v1/Accounts/{account_sid}/Calls/{sid}.json",
		params={"details": "true"},
		timeout=TIMEOUT,
	)
	response.raise_for_status()
	detailed = response.json().get("Call")
	if not isinstance(detailed, dict) or detailed.get("Sid") != sid:
		raise ValueError("Exotel detail response has a missing or mismatched CallSid")
	if detailed.get("AccountSid", account_sid) != account_sid:
		raise ValueError("Exotel detail response belongs to a different account")
	merged = {**call, **detailed}
	_duration(merged)
	return merged


def _resolve_call(call, sid, number, s3_url):
	_status(call)
	name = frappe.db.get_value("CRM Call Log", {"id": sid}, "name")
	if name:
		log = frappe.get_doc("CRM Call Log", name)
		if log.telephony_medium != "Exotel":
			raise ValueError("Existing CallSid belongs to a non-Exotel log")
		existing_number = log.get("from") if log.type == "Incoming" else log.to
		if re.sub(r"\D", "", cstr(existing_number))[-10:] != re.sub(r"\D", "", number)[-10:]:
			raise ValueError("Existing CallSid has a different customer phone number")
		if (log.type == "Incoming") != _is_inbound(call):
			raise ValueError("Existing CallSid has a different call direction")
		if log.recording_url and log.recording_url.startswith("s3://") and log.recording_url != s3_url:
			raise ValueError("Existing CallSid already points to a different S3 recording")
		references = []
		if log.reference_doctype and log.reference_docname:
			references.append((log.reference_doctype, log.reference_docname))
		references.extend(
			(row.link_doctype, row.link_name)
			for row in log.links
			if row.link_doctype in ("CRM Lead", "CRM Deal", "Contact") and row.link_name
		)
		if references:
			return log, list(dict.fromkeys(references))
	else:
		log = None
	contact = get_contact_by_phone_number(number)
	if contact.get("lead"):
		return log, [("CRM Lead", contact["lead"])]
	if contact.get("deal"):
		return log, [("CRM Deal", contact["deal"])]
	if contact.get("name"):
		return log, [("Contact", contact["name"])]
	return log, []


def _s3_has(s3, bucket, key, account_sid, sid):
	from botocore.exceptions import ClientError

	try:
		head = s3.head_object(Bucket=bucket, Key=key)
	except ClientError as exc:
		if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
			return False
		raise
	meta = head.get("Metadata", {})
	if (
		meta.get("exotel-account") != account_sid
		or meta.get("exotel-call") != sid
		or not re.fullmatch(r"[a-f0-9]{64}", meta.get("sha256", ""))
		or head["ContentLength"] <= 0
		or str(head["ContentLength"]) != meta.get("bytes")
	):
		raise ValueError("Existing S3 object lacks verified migration metadata; manual review required")
	return True


def _validate_recording_url(url):
	parts = urlsplit(url)
	if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
		raise ValueError("Recording URL must be an HTTPS URL without embedded credentials")


def _redact_url(url):
	if not url:
		return ""
	parts = urlsplit(url)
	return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


@contextmanager
def _recording_response(download, source_url, recording_auth):
	options = {"headers": {"Accept-Encoding": "identity"}, "stream": True, "timeout": TIMEOUT}
	parts = urlsplit(source_url)
	can_authenticate = (
		recording_auth
		and parts.scheme == "https"
		and parts.netloc == "recordings.exotel.com"
		and not parts.query
	)
	with download.get(source_url, **options) as response:
		if response.status_code != 401 or not can_authenticate:
			response.raise_for_status()
			yield response
			return
	# Do not attach old-account credentials to signed URLs or third-party storage.
	with download.get(source_url, auth=recording_auth, **options) as response:
		response.raise_for_status()
		yield response


def _upload(source_url, s3, bucket, key, account_sid, sid, *, recording_auth=None):
	with _session() as download, tempfile.TemporaryFile() as audio:
		with _recording_response(download, source_url, recording_auth) as response:
			if response.headers.get("Content-Encoding", "identity").lower() != "identity":
				raise ValueError("Encoded recording response prevents byte-length verification")
			if not response.headers.get("Content-Length"):
				raise ValueError("Recording response missing Content-Length; cannot verify completeness")
			expected = int(response.headers["Content-Length"])
			size, checksum = 0, hashlib.sha256()
			for chunk in response.iter_content(chunk_size=1024 * 1024):
				audio.write(chunk)
				size += len(chunk)
				checksum.update(chunk)
			if size <= 0 or size != expected:
				raise ValueError(f"Incomplete recording: expected {expected} bytes, received {size}")
			content_type = response.headers.get("Content-Type", "audio/mpeg").split(";")[0]
			if content_type not in ("audio/mpeg", "audio/mp3", "application/octet-stream"):
				raise ValueError(f"Unexpected recording content type: {content_type}")
		audio.seek(0)
		s3.upload_fileobj(
			audio,
			bucket,
			key,
			ExtraArgs={
				"ContentType": "audio/mpeg",
				"Metadata": {
					"exotel-account": account_sid,
					"exotel-call": sid,
					"sha256": checksum.hexdigest(),
					"bytes": str(size),
				},
			},
		)
	if not _s3_has(s3, bucket, key, account_sid, sid):
		raise ValueError("Uploaded S3 object not found")


def _upsert_call_log(call, sid, s3_url, log, references):
	is_new = log is None
	if log:
		log.recording_url = s3_url
	else:
		log = frappe.new_doc("CRM Call Log")
		log.id = sid
		log.telephony_medium = "Exotel"
		log.type = "Incoming" if _is_inbound(call) else "Outgoing"
		log.status = _status(call)
		log.to = cstr(_pick(call, "To", "to"))
		setattr(log, "from", cstr(_pick(call, "From", "from")))
		log.duration = _duration(call)
		log.start_time = _pick(call, "StartTime", "start_time", "DateCreated")
		log.end_time = _pick(call, "EndTime", "end_time")
		log.recording_url = s3_url
		# The default reference_doctype without a docname is a dangling Dynamic Link.
		# links is the canonical call/lead relationship; suppress that insert default.
		log.reference_doctype = None
		log.dont_update_if_missing.append("reference_doctype")
	for doctype, docname in references:
		log.link_with_reference_doc(doctype, docname)
	log.flags.crm_historical_import = True
	log.save(ignore_permissions=True)
	if is_new:
		# Frappe overwrites creation during insert; the call timeline uses this field.
		log.creation = get_datetime(call["DateCreated"])
		frappe.db.set_value("CRM Call Log", log.name, "creation", log.creation, update_modified=False)
	return log.name


def _attachment_url(log_name):
	return get_url(
		"/api/method/crm.integrations.api.get_recording_url?" + urlencode({"call_log_name": log_name})
	)


def _attachment_exists(doctype, docname, log_name):
	return frappe.db.exists(
		"File",
		{
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
			"file_url": _attachment_url(log_name),
		},
	)


def _attach_to_reference(doctype, docname, log_name, sid):
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"call-recording-{sid}.mp3",
			"file_url": _attachment_url(log_name),
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)

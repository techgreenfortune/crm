import frappe
import pytz
from frappe.exceptions import ValidationError
from frappe.integrations.utils import make_get_request
from frappe.utils import get_datetime, get_system_timezone

FB_GRAPH_API_BASE = "https://graph.facebook.com"
FB_GRAPH_API_VERSION = "v23.0"

PAID_LEAD_SOURCE = "Paid"
DEFAULT_SUB_SOURCE = "Meta Generic"

# Meta's standard Lead Ads question keys, mapped to their most common CRM Lead
# counterpart. Only fires when mapped_to_crm_field would otherwise be blank —
# never overwrites a value someone already set. Custom (advertiser-typed) questions
# won't match anything here and stay unmapped, same as before — manual mapping via
# the Questions grid still works for those.
STANDARD_QUESTION_FIELD_MAP = {
	"full_name": "first_name",
	"first_name": "first_name",
	"last_name": "last_name",
	"email": "email",
	"work_email": "email",
	"phone_number": "mobile_no",
	"phone": "mobile_no",
	"mobile_phone_number": "mobile_no",
	"city": "custom_city",
	"state": "custom_state",
	"zip_code": "custom_pincode",
	"post_code": "custom_pincode",
	"company_name": "organization",
}


def _auto_map_questions(questions: list[dict]) -> list[dict]:
	"""Auto-fill mapped_to_crm_field for recognized standard keys.

	Tracks targets already claimed (by an existing mapping or an earlier question
	in this same list) so two synonym questions on one form (e.g. "email" and
	"work_email") never both auto-map to the same CRM field — sync_single_lead's
	dict comprehension keys by target field name, so a silent collision would
	drop one answer with no error. Leave the second one unmapped; admin resolves
	it manually via the Questions grid if needed.
	"""
	mapped = []
	used_targets = {q.get("mapped_to_crm_field") for q in questions if q.get("mapped_to_crm_field")}
	for q in questions:
		q = dict(q)
		if not q.get("mapped_to_crm_field"):
			guess = STANDARD_QUESTION_FIELD_MAP.get((q.get("key") or "").strip().lower())
			if guess and guess not in used_targets:
				q["mapped_to_crm_field"] = guess
				used_targets.add(guess)
		mapped.append(q)
	return mapped


class DuplicateLeadError(ValidationError):
	pass


def fb_created_time_to_site_datetime(created_time: str):
	"""Meta returns an ISO-8601 UTC timestamp (e.g. "2026-09-07T05:23:11+0000").
	Frappe Datetime fields are naive-local — convert to the site timezone before
	storing, otherwise the lead's displayed submit time drifts from what Meta shows
	by the site's UTC offset."""
	utc_dt = get_datetime(created_time)
	if utc_dt is None:
		frappe.throw(frappe._("Invalid Facebook lead created_time: {0}").format(created_time))
	assert utc_dt is not None
	if utc_dt.tzinfo is None:
		utc_dt = pytz.UTC.localize(utc_dt)
	return utc_dt.astimezone(pytz.timezone(get_system_timezone())).replace(tzinfo=None)


def site_datetime_to_utc_timestamp(dt) -> int:
	"""Inverse of the above, for building the Graph API `GREATER_THAN` filter.
	Doesn't use frappe.utils.data.get_timestamp — that truncates to midnight
	(getdate() drops time-of-day) and resolves via the OS/process local timezone,
	not the site timezone, so it under- or over-shoots the real last-sync instant."""
	local_dt = get_datetime(dt)
	if local_dt is None:
		frappe.throw(frappe._("Invalid last_synced_at value: {0}").format(dt))
	assert local_dt is not None
	aware = pytz.timezone(get_system_timezone()).localize(local_dt)
	return int(aware.astimezone(pytz.UTC).timestamp())


def get_fb_graph_api_url(endpoint: str) -> str:
	if endpoint.startswith("/"):
		endpoint = endpoint[1:]

	return f"{FB_GRAPH_API_BASE}/{FB_GRAPH_API_VERSION}/{endpoint}"


class FacebookSyncSource:
	def __init__(
		self,
		access_token: str,
		form_id: str,
		source_name: str | None = None,
		sub_source: str | None = None,
	):
		self.access_token = access_token
		self.form_id = form_id
		self.source_name = source_name
		self.sub_source = sub_source
		self.form_questions_mapping = None

	def get_api_url(self, endpoint: str) -> str:
		return get_fb_graph_api_url(endpoint)

	def sync(self):
		if not frappe.db.exists("CRM Lead Source", PAID_LEAD_SOURCE):
			frappe.log_error(
				title="Facebook Lead Sync misconfigured",
				message=f"CRM Lead Source {PAID_LEAD_SOURCE!r} does not exist; aborting sync.",
			)
			return

		leads = self.fetch_leads()
		for lead in leads:
			self.sync_single_lead(lead)
		self.update_last_synced_at()

	def sync_single_lead(self, lead, raise_exception=False):
		question_to_field_map = self.get_form_questions_mapping()
		lead_data = {item["name"]: item["values"][0] for item in lead["field_data"]}
		crm_lead_data = {
			question_to_field_map.get(k): v for k, v in lead_data.items() if k in question_to_field_map
		}
		crm_lead_data["source"] = PAID_LEAD_SOURCE
		crm_lead_data["custom_sub_source"] = self.sub_source or DEFAULT_SUB_SOURCE
		crm_lead_data["facebook_lead_id"] = lead["id"]
		crm_lead_data["facebook_form_id"] = self.form_id

		try:
			self.validate_duplicate_lead(crm_lead_data, question_to_field_map)
			doc = frappe.get_doc(
				{
					"doctype": "CRM Lead",
					**crm_lead_data,
				}
			).insert(ignore_permissions=True)
		except (frappe.UniqueValidationError, DuplicateLeadError):
			self.create_failure_log(lead, "Duplicate")
			if raise_exception:
				raise
			return None
		except Exception:
			self.create_failure_log(lead, traceback=frappe.get_traceback(with_context=True))
			if raise_exception:
				raise
			return None

		if lead.get("created_time"):
			try:
				self._backdate_creation(doc, lead["created_time"])
			except Exception:
				frappe.log_error(
					title="Facebook lead backdate failed",
					message=f"Lead {doc.name} created but creation timestamp not backdated:\n{frappe.get_traceback(with_context=True)}",
				)

		return doc

	def _backdate_creation(self, doc, fb_created_time: str) -> None:
		"""`creation` gets stamped to now() unconditionally in
		Document.set_user_and_timestamp() before db_insert ever runs, so passing
		`creation` through crm_lead_data on the insert dict has no effect — it's
		overwritten before the INSERT. Patch it via a direct DB write after insert
		instead (the standard Frappe data-import pattern); update_modified=False
		so `modified` still reflects the real sync time, only `creation` (the
		"Created" column users see) is corrected to Meta's actual submit time."""
		frappe.db.set_value(
			"CRM Lead",
			doc.name,
			"creation",
			fb_created_time_to_site_datetime(fb_created_time),
			update_modified=False,
		)

	def fetch_leads(self):
		url = self.get_api_url(f"/{self.form_id}/leads")
		params = {
			"access_token": self.access_token,
			"fields": "id,created_time,field_data",
			"limit": 100000,  # TODO: pagination
		}

		filtering = []
		if self.last_synced_at:
			timestamp = site_datetime_to_utc_timestamp(self.last_synced_at)
			filtering.append({"field": "time_created", "operator": "GREATER_THAN", "value": timestamp})
			params["filtering"] = frappe.as_json(filtering)

		return make_get_request(
			url,
			params=params,
		).get("data", [])

	def get_form_questions_mapping(self):
		if self.form_questions_mapping:
			return self.form_questions_mapping

		form_questions = frappe.db.get_all(
			"Facebook Lead Form Question",
			filters={"parent": self.form_id},
			fields=["key", "mapped_to_crm_field"],
		)
		self.form_questions_mapping = {
			q["key"]: q["mapped_to_crm_field"] for q in form_questions if q["mapped_to_crm_field"]
		}

		return self.form_questions_mapping

	@property
	def last_synced_at(self):
		return frappe.db.get_value(
			"Lead Sync Source", self.source_name or {"facebook_lead_form": self.form_id}, "last_synced_at"
		)

	def create_failure_log(
		self, lead_data: dict | None = None, type: str = "Failure", traceback: str | None = None
	):
		return frappe.get_doc(
			{
				"doctype": "Failed Lead Sync Log",
				"type": type,
				"lead_data": frappe.as_json(lead_data),
				"source": self.get_source_name(),
				"traceback": traceback,
			}
		).insert(ignore_permissions=True)

	def update_last_synced_at(self):
		frappe.db.set_value(
			"Lead Sync Source",
			self.source_name or {"facebook_lead_form": self.form_id},
			"last_synced_at",
			frappe.utils.now(),
		)

	def get_source_name(self):
		if self.source_name:
			return self.source_name

		return frappe.db.get_value("Lead Sync Source", {"facebook_lead_form": self.form_id}, "name")

	def validate_duplicate_lead(self, lead_data: dict, field_mapping: dict):
		# field_mapping.values() is every field the FORM has mapped; lead_data only has
		# keys for fields THIS lead actually answered. A form can have more mapped
		# fields than a given lead filled in (an optional question left blank), so
		# filter to fields actually present rather than indexing blindly — auto-mapping
		# now wires up more optional fields (city/state/company) by default, making an
		# unanswered-but-mapped field a real, expected case, not just a theoretical one.
		validation_filters = {
			crm_field: lead_data[crm_field] for crm_field in field_mapping.values() if crm_field in lead_data
		}
		validation_filters["facebook_form_id"] = lead_data["facebook_form_id"]  # only for this campaign
		if frappe.db.exists("CRM Lead", validation_filters):
			raise DuplicateLeadError


@frappe.whitelist()
def fetch_and_store_pages_from_facebook(access_token: str) -> list[dict]:
	if not access_token:
		frappe.throw(frappe._("Access token is required"))

	account_details = get_fb_account_details(access_token)
	if not account_details.get("id"):
		frappe.throw(frappe._("Invalid access token provided for Facebook."))

	url = get_fb_graph_api_url("/me/accounts")
	pages = make_get_request(url, params={"access_token": access_token}).get("data", [])
	for page in pages:
		page_id = page["id"]
		already_synced = frappe.db.exists("Facebook Page", page_id)
		if not already_synced:
			create_facebook_page_in_db(page, account_details)
		forms = fetch_and_store_leadgen_forms_from_facebook(page_id, page["access_token"])
		page["forms"] = forms

	return pages


def get_fb_account_details(access_token: str) -> dict:
	url = get_fb_graph_api_url("me")
	try:
		response = make_get_request(url, params={"access_token": access_token})
	except Exception as _:
		frappe.throw(frappe._("Please check your access token"))
	return response


def create_facebook_page_in_db(page: dict, account_details: dict) -> None:
	frappe.get_doc(
		{
			"doctype": "Facebook Page",
			"page_name": page["name"],
			"id": page["id"],
			"category": page["category"],
			"access_token": page["access_token"],
			"account_id": account_details["id"],
		}
	).insert(ignore_permissions=True)


def fetch_and_store_leadgen_forms_from_facebook(page_id: str, page_access_token: str) -> list[dict]:
	fields = "id,name,questions"
	url = get_fb_graph_api_url(f"/{page_id}/leadgen_forms")
	forms = make_get_request(
		url,
		params={
			"access_token": page_access_token,
			"fields": fields,
			"limit": 15000,
		},
	).get("data", [])
	for form in forms:
		form_id = form["id"]
		already_synced = frappe.db.exists("Facebook Lead Form", form_id)
		if already_synced:
			continue
		try:
			create_facebook_lead_form_in_db(form, page_id)
		except frappe.ValidationError:
			# check_mandatory_crm_fields_mapped (facebook_lead_form.py) rejects a form
			# with no first_name mapping — e.g. a fully custom name question
			# _auto_map_questions doesn't recognize. One bad form must not abort
			# discovery for every other page/form in this same token-connect flow;
			# skip it, log for visibility, admin maps it manually via Desk if needed.
			frappe.log_error(
				title="Facebook Lead Form not created",
				message=f"Form {form_id!r} ({form.get('name')!r}) on page {page_id!r} "
				f"has no recognized name field mapping; skipped.",
			)

	return forms


def create_facebook_lead_form_in_db(form: dict, page_id: str) -> None:
	form_doc = frappe.get_doc(
		{
			"doctype": "Facebook Lead Form",
			"form_name": form["name"],
			"id": form["id"],
			"page": page_id,
			"questions": _auto_map_questions(form["questions"]),
		}
	)
	form_doc.insert(ignore_permissions=True)


@frappe.whitelist()
def get_pages_with_forms() -> list[dict]:
	pages = frappe.db.get_all("Facebook Page", fields=["id", "name"])
	for page in pages:
		forms = frappe.db.get_all("Facebook Lead Form", filters={"page": page["id"]}, fields=["id", "name"])
		page["forms"] = forms
	return pages

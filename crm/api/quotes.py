import frappe
from frappe import _

from crm.integrations.brevo.quote_emails import fire_quote_requested
from crm.utils import notify_role_users


def _notify_estimation_team_on_quote_request(
	qr_name: str, lead_name: str, lead_display: str, requester: str
) -> None:
	"""Send a Notification Log to every active Estimation Team member.

	Delegates to ``crm.utils.notify_role_users`` — the fan-out pattern is
	shared with the Stage Side Effects server script and other callers.
	"""
	requester_name = frappe.get_cached_value("User", requester, "full_name") or requester
	subject = f"Quote requested for {lead_display}"
	email_content = (
		f"<p><b>{requester_name}</b> has requested a quote for lead "
		f"<b>{lead_display}</b> ({lead_name}).</p>"
		f"<p>Please open Quote Request <b>{qr_name}</b>, attach the quote "
		f"file, fill in Quote Value, Margin and Area, then set the status "
		f"to <em>Quote Received</em>.</p>"
	)
	notify_role_users(
		role="Estimation Team",
		subject=subject,
		email_content=email_content,
		document_type="CRM Quote Request",
		document_name=qr_name,
		skip_user=requester,
	)
	fire_quote_requested(qr_name)


@frappe.whitelist()
def list_lead_quote_requests(lead: str) -> list[dict]:
	"""Return QR cards for a lead with the image URLs for each.

	Mirrors the fields the Quotes-tab card needs (name, status, value, margin,
	file, validity, external quote number, area), plus a derived ``images``
	list from the ``images`` child table — which ``frappe.client.get_list``
	cannot aggregate.

	Permission model: gated on Quote Request read (NOT Lead read). Estimation
	Team has full read on QRs but the CRM Lead Permission Query restricts them
	to C2 leads — so checking Lead read would 403 them on closed-lead QR
	history. The Quote Request Permission Query already enforces row-level
	visibility (lead_owner / assignment / Estimation bypass), so this endpoint
	only needs to verify the doctype-level read perm and that the lead exists.
	"""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead {0} not found").format(lead), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Quote Request", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# `get_list` (not `get_all`) so the Quote Request Permission Query runs.
	qrs = frappe.get_list(
		"CRM Quote Request",
		filters={"lead": lead},
		fields=[
			"name",
			"status",
			"is_superseded",
			"quote_value",
			"quote_margin",
			"quote_validity",
			"quote_sq_ft",
			"quote_number",
			"quote_file",
			"requested_on",
			"requested_by",
			"modified",
			"notes",
		],
		order_by="modified desc",
		limit=50,
	)
	if not qrs:
		return []

	# Child rows: safe to read with get_all since visibility is already gated
	# by their parent QR being in the filtered set above.
	image_rows = frappe.get_all(
		"CRM Quote Revision Image",
		filters={
			"parenttype": "CRM Quote Request",
			"parent": ["in", [q["name"] for q in qrs]],
		},
		fields=["parent", "image"],
		order_by="idx asc",
	)
	image_map: dict[str, list[str]] = {}
	for row in image_rows:
		image_map.setdefault(row["parent"], []).append(row["image"])
	for q in qrs:
		q["images"] = image_map.get(q["name"], [])
	return qrs


@frappe.whitelist()
def request_quote(lead: str, notes: str = "", images: list | None = None) -> str:
	from crm.overrides import crm_lead_permissions

	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)

	lead_doc = frappe.get_doc("CRM Lead", lead, ignore_permissions=True)

	user = frappe.session.user
	if user != "Administrator":
		if not crm_lead_permissions.has_permission(lead_doc, "write", user):
			frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.has_permission("CRM Quote Request", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# Stage gate: belt-and-suspenders against direct API calls (UI hides button at C4).
	if lead_doc.status == "C4":
		frappe.throw(
			_("Quote requests are not allowed on leads at C4 (Won)."),
			frappe.ValidationError,
		)

	# Mutual-exclusion lock: prevents two rapid concurrent requests from both
	# passing the guard and each inserting a Pending QR for the same lead.
	# set_value lacks NX; SETNX needed for atomic test-and-set lock.
	_lock_key = f"crm:qr_request:{lead}"
	if not frappe.cache().set(_lock_key, 1, ex=30, nx=True):  # nosemgrep: frappe-cache-breaks-multitenancy
		frappe.throw(
			_("A quote request is already being processed for this lead. Try again in a moment."),
			frappe.ValidationError,
		)
	try:
		existing = frappe.db.get_value(
			"CRM Quote Request",
			{"lead": lead, "status": ["!=", "Accepted"]},
			"name",
			order_by="creation desc",
		)
		if existing:
			return existing

		# Supersede all prior QRs for this lead (they are all Accepted at this
		# point — the guard above would have returned early otherwise).
		frappe.db.set_value(
			"CRM Quote Request",
			{"lead": lead, "is_superseded": 0},
			"is_superseded",
			1,
			update_modified=False,
		)

		# Pre-fill estimation fields from current lead values as reference for estimation team.
		lead_vals = (
			frappe.db.get_value(
				"CRM Lead",
				lead,
				[
					"custom_tentative_value",
					"custom_tentative_area_sqft",
					"custom_final_margin",
					"custom_total_quantity",
				],
				as_dict=True,
			)
			or {}
		)

		qr = frappe.new_doc("CRM Quote Request")
		qr.update({"lead": lead, "status": "Pending"})
		qr.notes = notes or ""
		qr.quote_value = lead_vals.get("custom_tentative_value") or 0
		qr.quote_sq_ft = lead_vals.get("custom_tentative_area_sqft") or 0
		qr.quote_margin = lead_vals.get("custom_final_margin") or 0
		qr.total_quantity = lead_vals.get("custom_total_quantity") or 0
		for url in images or []:
			qr.append("images", {"image": url})
		qr.flags.ignore_mandatory = True
		qr.insert()

		if not frappe.db.exists(
			"CRM Task",
			{
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
				"task_type": "upload_quote",
				"status": ["in", ["Todo", "In Progress"]],
			},
		):
			notes_snippet = f" Notes: {notes}." if notes else ""
			frappe.get_doc(
				{
					"doctype": "CRM Task",
					"task_type": "upload_quote",
					"title": f"Upload Quote — {lead_doc.lead_name or lead}",
					"status": "Todo",
					"priority": "High",
					"due_date": frappe.utils.add_days(frappe.utils.today(), 2),
					"reference_doctype": "CRM Lead",
					"reference_docname": lead,
					"quote_request": qr.name,
					"description": (
						f"Quote requested for lead {lead_doc.lead_name}.{notes_snippet} "
						f"Open Quote Request {qr.name}, attach the quote file, enter Quote Value, "
						"Margin and Area, then set status to Quote Received."
					),
				}
			).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead,
				"content": f"[AUTOMATION] Quote Request manually initiated by {frappe.session.user}.",
			}
		).insert(ignore_permissions=True)

		_notify_estimation_team_on_quote_request(
			qr_name=qr.name,
			lead_name=lead,
			lead_display=lead_doc.lead_name or lead,
			requester=frappe.session.user,
		)

		return qr.name
	finally:
		frappe.cache().delete(_lock_key)

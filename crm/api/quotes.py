import frappe
from frappe import _


@frappe.whitelist()
def list_lead_quote_requests(lead: str) -> list[dict]:
	"""Return QR cards for a lead with the revision-image count for each.

	Mirrors the fields the Quotes-tab card needs (name, status, value, margin,
	file, validity, external quote number, area), plus a derived `images_count`
	from the `revision_images` child table — which `frappe.client.get_list`
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
			"quote_value",
			"quote_margin",
			"quote_validity",
			"quote_sq_ft",
			"quote_number",
			"quote_file",
			"requested_on",
			"requested_by",
			"modified",
			"revision_notes",
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
	counts: dict[str, int] = {}
	image_map: dict[str, list[str]] = {}
	for row in image_rows:
		counts[row["parent"]] = counts.get(row["parent"], 0) + 1
		image_map.setdefault(row["parent"], []).append(row["image"])
	for q in qrs:
		q["images_count"] = counts.get(q["name"], 0)
		q["revision_images"] = image_map.get(q["name"], [])
	return qrs


@frappe.whitelist()
def request_quote(lead: str) -> str:
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Quote Request", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# Stage gate: Request Quote is meaningful only while the lead is still
	# open. At C4 (Won) the quote fields are frozen and a new QR would just
	# clutter the history. UI also hides the button at C4 — backend check is
	# belt-and-suspenders against direct API calls.
	lead_stage = frappe.db.get_value("CRM Lead", lead, "status")
	if lead_stage == "C4":
		frappe.throw(
			_("Quote requests are not allowed on leads at C4 (Won)."),
			frappe.ValidationError,
		)

	existing = frappe.db.get_value(
		"CRM Quote Request",
		{"lead": lead, "status": ["!=", "Accepted"]},
		"name",
		order_by="creation desc",
	)
	if existing:
		return existing

	lead_doc = frappe.get_doc("CRM Lead", lead)
	qr = frappe.new_doc("CRM Quote Request")
	qr.update({"lead": lead, "status": "Pending"})
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
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"task_type": "upload_quote",
				"title": f"Upload Quote — {lead_doc.lead_name or lead}",
				"status": "Todo",
				"priority": "High",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
				"description": (
					f"Quote manually requested for lead {lead_doc.lead_name}. "
					"Open the Quote Request, attach the quote file, enter Quote Value, "
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
	return qr.name

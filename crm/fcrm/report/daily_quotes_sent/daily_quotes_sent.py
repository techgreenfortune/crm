"""Quotes sent in a date range (default: today), split into New vs Revised.

"Sent" = the moment a CRM Quote Request first moves to "Quote Received"
(Estimation uploads the quote; the "Review & Share" notification fires).
The timestamp comes from the QR's Version log (track_changes=1). QRs inserted
directly in a non-Pending status (data import) have no such Version, so their
creation time is used instead.

Each revision round is its own QR (the old one is superseded), so a QR is
"Revised" when an older QR exists for the same lead. Accepted -> Quote Received
moves are a manager undo, not a send, and are ignored.
"""

import json

import frappe
from frappe import _
from frappe.utils import getdate, today

QR = "CRM Quote Request"


def execute(filters=None):
	filters = filters or {}
	from_date = getdate(filters.get("from_date") or today())
	to_date = getdate(filters.get("to_date") or from_date)
	return get_columns(), get_data(from_date, to_date)


def get_columns():
	return [
		{"label": _("Sent On"), "fieldname": "sent_on", "fieldtype": "Datetime", "width": 160},
		{"label": _("Type"), "fieldname": "quote_type", "fieldtype": "Data", "width": 90},
		{"label": _("Round"), "fieldname": "round", "fieldtype": "Data", "width": 70},
		{"label": _("Lead"), "fieldname": "lead", "fieldtype": "Link", "options": "CRM Lead", "width": 140},
		{"label": _("Lead Name"), "fieldname": "lead_name", "fieldtype": "Data", "width": 180},
		{"label": _("Lead Owner"), "fieldname": "lead_owner", "fieldtype": "Data", "width": 150},
		{"label": _("Quote Request"), "fieldname": "name", "fieldtype": "Link", "options": QR, "width": 150},
		{"label": _("Quote No"), "fieldname": "quote_number", "fieldtype": "Data", "width": 130},
		{"label": _("Quote Value"), "fieldname": "quote_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Margin %"), "fieldname": "quote_margin", "fieldtype": "Percent", "width": 90},
		{"label": _("Area (sqft)"), "fieldname": "quote_sq_ft", "fieldtype": "Float", "width": 100},
		{"label": _("Prepared By"), "fieldname": "prepared_by", "fieldtype": "Data", "width": 130},
		{"label": _("Current Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
	]


def get_sent_times(from_date, to_date) -> dict:
	"""{qr_name: sent datetime} for QRs first sent within [from_date, to_date]."""
	window = [from_date, to_date]  # frappe expands Date bounds to whole days on Datetime fields
	sent = {}

	versions = frappe.get_all(
		"Version",
		filters={
			"ref_doctype": QR,
			"creation": ["between", window],
			"data": ["like", '%"Quote Received"%'],
		},
		fields=["docname", "creation", "data"],
		order_by="creation asc",
	)
	for v in versions:
		changed = json.loads(v.data or "{}").get("changed") or []
		if any(f == "status" and new == "Quote Received" and old != "Accepted" for f, old, new in changed):
			sent.setdefault(v.docname, v.creation)

	# Imported QRs: created already past Pending, so no status-change Version exists.
	imported = frappe.get_all(
		QR,
		filters={"creation": ["between", window], "status": ["!=", "Pending"]},
		fields=["name", "creation"],
	)
	tracked = set(
		frappe.get_all(
			"Version",
			filters={
				"ref_doctype": QR,
				"docname": ["in", [q.name for q in imported] or [""]],
				"data": ["like", '%"Quote Received"%'],
			},
			pluck="docname",
		)
	)
	for q in imported:
		if q.name not in tracked:
			sent.setdefault(q.name, q.creation)

	return sent


def get_data(from_date, to_date):
	sent = get_sent_times(from_date, to_date)
	if not sent:
		return []

	rows = frappe.db.sql(
		"""
		SELECT qr.name, qr.lead, qr.quote_number, qr.quote_value, qr.quote_margin,
			qr.quote_sq_ft, qr.prepared_by, qr.status,
			COALESCE(l.lead_name, qr.lead_name) AS lead_name,
			COALESCE(u.full_name, l.lead_owner) AS lead_owner,
			(SELECT COUNT(*) FROM `tabCRM Quote Request` p
				WHERE p.lead = qr.lead AND p.creation <= qr.creation) AS round_no
		FROM `tabCRM Quote Request` qr
		LEFT JOIN `tabCRM Lead` l ON l.name = qr.lead
		LEFT JOIN `tabUser` u ON u.name = l.lead_owner
		WHERE qr.name IN %(names)s
		""",
		{"names": list(sent)},
		as_dict=True,
	)
	for r in rows:
		r.sent_on = sent[r.name]
		r.quote_type = _("Revised") if r.round_no > 1 else _("New")
		r["round"] = f"R{r.pop('round_no')}"

	return sorted(rows, key=lambda r: (r.quote_type != _("New"), r.sent_on))

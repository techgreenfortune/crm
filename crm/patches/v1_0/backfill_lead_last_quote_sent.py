"""Backfill CRM Lead.custom_quotation_sent_on / custom_last_quote_type from quote history.

Going forward CRMQuoteRequest._sync_tentative_value_to_lead stamps these on every
send. For existing leads we rebuild them from the Quote Request Version log:
"sent" = first Pending -> Quote Received change; QRs imported straight into a
later status have no such Version, so their creation time is used.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	# Fixtures sync after patches, so make sure the new column exists first.
	if not frappe.db.has_column("CRM Lead", "custom_last_quote_type"):
		create_custom_field(
			"CRM Lead",
			{
				"fieldname": "custom_last_quote_type",
				"fieldtype": "Select",
				"label": "Last Quote Type",
				"options": "\nNew\nRevised",
				"insert_after": "custom_quotation_sent_on",
				"read_only": 1,
			},
		)

	rows = frappe.db.sql(
		"""
		SELECT s.lead, s.sent_on, IF(s.earlier > 0, 'Revised', 'New') AS quote_type
		FROM (
			SELECT
				q.lead,
				COALESCE(
					(SELECT MIN(v.creation) FROM `tabVersion` v
						WHERE v.ref_doctype = 'CRM Quote Request' AND v.docname = q.name
						AND v.data REGEXP '"status",[[:space:]]*"(Pending|Revision Requested)",[[:space:]]*"Quote Received"'),
					IF(q.status <> 'Pending' AND NOT EXISTS (
							SELECT 1 FROM `tabVersion` v2
							WHERE v2.ref_doctype = 'CRM Quote Request' AND v2.docname = q.name
							AND v2.data REGEXP '"Quote Received"'),
						q.creation, NULL)
				) AS sent_on,
				(SELECT COUNT(*) FROM `tabCRM Quote Request` p
					WHERE p.lead = q.lead AND p.creation < q.creation) AS earlier
			FROM `tabCRM Quote Request` q
			WHERE q.lead IS NOT NULL
		) s
		WHERE s.sent_on IS NOT NULL
		ORDER BY s.sent_on
		""",
		as_dict=True,
	)

	latest = {r.lead: r for r in rows}  # ordered by sent_on, so the last one per lead wins
	for lead, r in latest.items():
		frappe.db.set_value(
			"CRM Lead",
			lead,
			{"custom_quotation_sent_on": r.sent_on, "custom_last_quote_type": r.quote_type},
			update_modified=False,
		)

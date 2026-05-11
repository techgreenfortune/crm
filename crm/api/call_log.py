import frappe
from frappe import _
from frappe.utils import today


def _add_lead_comment(lead_name: str, content: str) -> None:
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "CRM Lead",
				"reference_name": lead_name,
				"content": content,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Retry audit comment failed")


@frappe.whitelist()
def register_no_answer(lead_name: str) -> None:
	if frappe.db.exists("CRM Retry Log", {"lead": lead_name, "status": ["in", ["Active", "Paused"]]}):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "CRM Retry Log",
				"lead": lead_name,
				"status": "Active",
				"attempt_count": 0,
				"day_in_sequence": 1,
				"last_attempt_date": today(),
				"next_attempt_date": today(),  # due same day; scheduler fires end-of-business
			}
		).insert(ignore_permissions=True)
		_add_lead_comment(lead_name, "Retry sequence started — Day 1.")
	except frappe.DuplicateEntryError:
		pass  # concurrent request already inserted; safe to ignore


@frappe.whitelist()
def cancel_retry_log(lead_name: str, permanent: bool = True) -> None:
	existing = frappe.db.get_value(
		"CRM Retry Log",
		{"lead": lead_name, "status": ["in", ["Active", "Paused"]]},
		"name",
	)
	if existing:
		log = frappe.get_doc("CRM Retry Log", existing)
		log.status = "Cancelled" if permanent else "Paused"
		log.next_attempt_date = None
		log.save(ignore_permissions=True)
		if permanent:
			_add_lead_comment(lead_name, "Retry sequence cancelled — lead progressed out of retry path.")
		else:
			_add_lead_comment(lead_name, "Retry sequence paused — callback scheduled.")

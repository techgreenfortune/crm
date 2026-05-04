import frappe
from frappe import _
from frappe.utils import add_days, today

# Maps current retry day → (next_day, days_offset_from_today)
# Sequence per PRD §5.2: Day 1 (3 attempts), Day 2 (2), Day 3 (1), Day 5 (1), Day 7 (1), Day 12 (1 — final)
RETRY_SEQUENCE = {
	1:  (2,  1),
	2:  (3,  1),
	3:  (5,  2),
	5:  (7,  2),
	7:  (12, 5),
	12: (None, None),
}

RETRY_ATTEMPTS_PER_DAY = {1: 3, 2: 2, 3: 1, 5: 1, 7: 1, 12: 1}
MAX_TOTAL_ATTEMPTS = sum(RETRY_ATTEMPTS_PER_DAY.values())  # 9


def _add_lead_comment(lead_name: str, content: str) -> None:
	try:
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "CRM Lead",
			"reference_name": lead_name,
			"content": content,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Retry audit comment failed")


def register_no_answer(lead_name: str) -> None:
	existing = frappe.db.get_value(
		"CRM Retry Log",
		{"lead": lead_name, "status": ["in", ["Active", "Paused"]]},
		["name", "attempt_count", "day_in_sequence"],
		as_dict=True,
	)

	if existing:
		# Record the manual call attempt. Sequence day advancement and exhaustion
		# are owned by the scheduler (retry_engine.py), not by disposition logging.
		log = frappe.get_doc("CRM Retry Log", existing.name)
		if log.attempt_count < MAX_TOTAL_ATTEMPTS:
			log.attempt_count += 1
			log.last_attempt_date = today()
			log.save()
		_add_lead_comment(
			lead_name,
			f"Manual call attempt logged (total attempts: {log.attempt_count}). "
			"Retry engine will advance the sequence on the next scheduled date.",
		)
	else:
		# Guard against race-condition duplicates before insert
		if frappe.db.exists("CRM Retry Log", {"lead": lead_name, "status": ["in", ["Active", "Paused"]]}):
			return
		_, offset = RETRY_SEQUENCE[1]
		try:
			frappe.get_doc({
				"doctype": "CRM Retry Log",
				"lead": lead_name,
				"status": "Active",
				"attempt_count": 1,
				"day_in_sequence": 1,
				"last_attempt_date": today(),
				"next_attempt_date": add_days(today(), offset),
			}).insert(ignore_permissions=True)
			_add_lead_comment(lead_name, "Retry sequence started — Day 1 attempt logged.")
		except frappe.DuplicateEntryError:
			pass  # concurrent request already inserted; safe to ignore


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



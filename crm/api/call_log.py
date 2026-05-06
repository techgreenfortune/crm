import frappe
from frappe import _
from frappe.utils import add_days, today

# Maps current retry day → (next_day, days_offset_from_today)
# Sequence per PRD §5.2: Day 1 (3 attempts), Day 2 (2), Day 3 (1), Day 5 (1), Day 7 (1), Day 12 (1 — final)
RETRY_SEQUENCE = {
	1: (2, 1),
	2: (3, 1),
	3: (5, 2),
	5: (7, 2),
	7: (12, 5),
	12: (None, None),
}

RETRY_ATTEMPTS_PER_DAY = {1: 3, 2: 2, 3: 1, 5: 1, 7: 1, 12: 1}
DAY_SEQUENCE_ORDER = [1, 2, 3, 5, 7, 12]
MAX_TOTAL_ATTEMPTS = sum(RETRY_ATTEMPTS_PER_DAY.values())  # 9


def _day_threshold(day: int) -> int:
	"""Cumulative No Answer count at which that day's call quota is met and WhatsApp fires."""
	total = 0
	for d in DAY_SEQUENCE_ORDER:
		total += RETRY_ATTEMPTS_PER_DAY[d]
		if d == day:
			return total
	return total


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


def _advance_or_exhaust(log) -> None:
	"""
	Called when a day's No Answer quota is fully logged.
	Enqueues WhatsApp for the completed day then advances the sequence or marks exhausted.
	"""
	old_day = log.day_in_sequence

	frappe.enqueue(
		"crm.api.retry_engine.send_retry_whatsapp",
		lead_name=log.lead,
		day=old_day,
	)

	next_day, offset = RETRY_SEQUENCE.get(old_day, (None, None))
	if next_day:
		frappe.db.set_value(
			"CRM Retry Log",
			log.name,
			{
				"day_in_sequence": next_day,
				"next_attempt_date": add_days(today(), offset),
			},
		)
		_add_lead_comment(
			log.lead,
			f"Day {old_day} calls done — WhatsApp sent. "
			f"Next retry: Day {next_day} on {add_days(today(), offset)}.",
		)
	else:
		frappe.db.set_value(
			"CRM Retry Log",
			log.name,
			{
				"status": "Exhausted",
				"next_attempt_date": None,
			},
		)
		_add_lead_comment(log.lead, "Retry sequence exhausted — Day 12 complete. Lead moved to Cold.")
		lead_doc = frappe.get_doc("CRM Lead", log.lead)
		lead_doc.status = "Cold"
		lead_doc.save(ignore_permissions=True)


def register_no_answer(lead_name: str) -> None:
	existing = frappe.db.get_value(
		"CRM Retry Log",
		{"lead": lead_name, "status": ["in", ["Active", "Paused"]]},
		["name", "attempt_count", "day_in_sequence"],
		as_dict=True,
	)

	if existing:
		log = frappe.get_doc("CRM Retry Log", existing.name)
		if log.attempt_count < MAX_TOTAL_ATTEMPTS:
			log.attempt_count += 1
			log.last_attempt_date = today()
			log.save()

		expected = RETRY_ATTEMPTS_PER_DAY.get(log.day_in_sequence, 1)
		_add_lead_comment(
			lead_name,
			f"No Answer logged — attempt {log.attempt_count}/{MAX_TOTAL_ATTEMPTS} "
			f"(Day {log.day_in_sequence}, {expected} expected this day).",
		)

		# WhatsApp fires only when all call attempts for the current day are exhausted
		if log.attempt_count >= _day_threshold(log.day_in_sequence):
			_advance_or_exhaust(log)
	else:
		# Guard against race-condition duplicates before insert
		if frappe.db.exists("CRM Retry Log", {"lead": lead_name, "status": ["in", ["Active", "Paused"]]}):
			return
		try:
			frappe.get_doc(
				{
					"doctype": "CRM Retry Log",
					"lead": lead_name,
					"status": "Active",
					"attempt_count": 1,
					"day_in_sequence": 1,
					"last_attempt_date": today(),
					"next_attempt_date": add_days(today(), RETRY_SEQUENCE[1][1]),
				}
			).insert(ignore_permissions=True)
			_add_lead_comment(
				lead_name,
				f"Retry sequence started — Day 1, attempt 1 of {RETRY_ATTEMPTS_PER_DAY[1]}.",
			)
			# Day 1 needs {RETRY_ATTEMPTS_PER_DAY[1]} No Answer calls before WhatsApp fires
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

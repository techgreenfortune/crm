from typing import Any, cast

import frappe
from frappe import _
from frappe.utils import today


def add_lead_comment(lead_name: str, content: str, source: str = "SYSTEM_RETRY") -> None:
	# `source` tags the audit trail with a `source_of_change` prefix so reporting can
	# bucket comments by origin (SCHEDULER / SYSTEM_RETRY / API_SUBMIT / USER_ACTION).
	# Default is SYSTEM_RETRY because every existing caller of this helper is in the
	# retry/save path; scheduler-driven callers pass source="SCHEDULER" explicitly.
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "CRM Lead",
				"reference_name": lead_name,
				"content": f"[{source}] {content}",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Retry audit comment failed")


@frappe.whitelist()
def register_no_answer(lead_name: str) -> None:
	# Gate: only C0 leads or leads with engagement in (Cold-Unresponsive, Reactivated)
	# enter the retry sequence. Mirrors the server-script gate at
	# fixtures/server_script.json "After Save — Call Not Answered Retry Trigger" — kept here
	# as defense-in-depth so direct callers (console, REST, future server scripts)
	# can't bypass.
	state = frappe.db.get_value("CRM Lead", lead_name, ["status", "lead_status"], as_dict=True) or {}
	if state.get("status") != "C0" and state.get("lead_status") not in (
		"Cold-Unresponsive",
		"Reactivated",
	):
		return

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
		add_lead_comment(lead_name, "Retry sequence started — Day 1.")
		# Defensive cleanup: two concurrent No-Answer saves can both pass the
		# exists() guard above and both call insert() — DuplicateEntryError only
		# catches the hash-name collision path, but rows are named randomly so two
		# Active logs on the same lead can still land. Cancel runner-ups.
		extras = frappe.get_all(
			"CRM Retry Log",
			filters={"lead": lead_name, "status": "Active"},
			fields=["name"],
			order_by="creation asc",
		)
		for row in extras[1:]:
			frappe.db.set_value(
				"CRM Retry Log",
				row["name"],
				{"status": "Cancelled", "next_attempt_date": None},
			)
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
		# get_doc accepts the unique name as a str; stub return type is over-broad.
		# cast log to Any because dynamic doctype fields aren't on the Document stub.
		log = cast(Any, frappe.get_doc("CRM Retry Log", cast(str, existing)))
		log.status = "Cancelled" if permanent else "Paused"
		log.next_attempt_date = None
		log.save(ignore_permissions=True)
		if permanent:
			add_lead_comment(lead_name, "Retry sequence cancelled — lead progressed out of retry path.")
		else:
			add_lead_comment(lead_name, "Retry sequence paused — callback scheduled.")

from __future__ import annotations

from typing import Any, cast

import frappe
from frappe.utils import add_days, today

from crm.api.call_log import add_lead_comment

RETRY_CADENCE: dict[int, int | None] = {1: 2, 2: 3, 3: 5, 5: 7, 7: 12, 12: None}
RETRY_ACTIVE_STATUS: tuple[str, ...] = ("C0",)
RETRY_ACTIVE_LEAD_STATUSES: tuple[str, ...] = ("Active", "Cold-Unresponsive", "Reactivated")


def _is_retry_active(status: str | None, lead_status: str | None) -> bool:
	return status in RETRY_ACTIVE_STATUS and lead_status in RETRY_ACTIVE_LEAD_STATUSES


@frappe.whitelist()
def send_retry_whatsapp(lead_name: str, day: int) -> None:
	"""Send AiSensy follow-up message. Called via frappe.enqueue from Scheduler Event Server Script."""

	# Re-check: small window between scheduler enqueue and worker pickup
	current = cast(
		"dict[str, Any] | None",
		frappe.db.get_value(
			"CRM Lead",
			lead_name,
			["status", "lead_status"],  # pyright: ignore[reportArgumentType]
			as_dict=True,
		),
	)
	if not current or not _is_retry_active(current.get("status"), current.get("lead_status")):
		frappe.logger().info(
			f"[RetryEngine] Lead {lead_name} moved out of retry-active set — skipping Day {day} WhatsApp"
		)
		return

	from crm.integrations.aisensy.aisensy_handler import (
		get_aisensy_settings,
		is_aisensy_enabled,
		send_template_message,
	)

	if not is_aisensy_enabled():
		frappe.logger().info(f"[RetryEngine] AiSensy disabled — skipping Day {day} WhatsApp for {lead_name}")
		return

	settings = get_aisensy_settings()
	template_name = getattr(settings, "retry_followup_template", None)
	if not template_name:
		frappe.logger().info(
			f"[RetryEngine] retry_followup_template not configured — skipping Day {day} WhatsApp for {lead_name}"
		)
		return

	lead = cast(
		"dict[str, Any] | None",
		frappe.db.get_value(
			"CRM Lead",
			lead_name,
			["mobile_no", "lead_name"],  # pyright: ignore[reportArgumentType]
			as_dict=True,
		),
	)
	if not lead:
		return

	phone = lead.get("mobile_no") or ""
	if not phone:
		frappe.logger().info(f"[RetryEngine] No mobile_no on lead {lead_name} — skipping WhatsApp")
		return

	display_name = lead.get("lead_name") or lead_name
	try:
		send_template_message(
			to=phone,
			template_name=template_name,
			variables=["$FirstName"],
			reference_doctype="CRM Lead",
			reference_name=lead_name,
			recipient_name=display_name,
			params_fallback_value={"FirstName": display_name},
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"RetryEngine: AiSensy send failed for lead {lead_name} (template: {template_name})",
		)


@frappe.whitelist()
def advance_retry_sequence() -> dict[str, int]:
	"""Scheduler-driven daily advancement of all Active CRM Retry Log rows due today.

	For each due log: enqueue Day-N WhatsApp, advance day_in_sequence per PRD §5.2
	cadence (1→2→3→5→7→12), or mark Exhausted + auto-move lead to Cold on Day 12.
	Defensive: marks log Cancelled if lead has moved out of retry-active set.
	"""
	summary: dict[str, int] = {
		"checked": 0,
		"advanced": 0,
		"exhausted": 0,
		"cancelled": 0,
		"skipped": 0,
		"errors": 0,
	}

	due_logs = frappe.get_all(
		"CRM Retry Log",
		filters={"status": "Active", "next_attempt_date": ["<=", today()]},
		fields=["name", "lead", "day_in_sequence", "attempt_count"],
	)
	summary["checked"] = len(due_logs)

	for row in due_logs:
		try:
			_advance_one(row, summary)
		except Exception:
			summary["errors"] += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"RetryEngine: advance failed for log {row['name']} (lead {row['lead']})",
			)

	frappe.logger().info(f"[RetryEngine] advance_retry_sequence summary: {summary}")
	return summary


def _advance_one(row: dict[str, Any], summary: dict[str, int]) -> None:
	lead_name: str = row["lead"]
	current_day = int(row["day_in_sequence"] or 0)
	log_name: str = row["name"]

	current = cast(
		"dict[str, Any] | None",
		frappe.db.get_value(
			"CRM Lead",
			lead_name,
			["status", "lead_status"],  # pyright: ignore[reportArgumentType]
			as_dict=True,
		),
	)
	if not current or not _is_retry_active(current.get("status"), current.get("lead_status")):
		frappe.db.set_value(
			"CRM Retry Log",
			log_name,
			{"status": "Cancelled", "next_attempt_date": None},
		)
		summary["cancelled"] += 1
		return

	# Atomic claim via attempt_count compare-and-swap: only proceed if THIS worker
	# successfully increments attempt_count from the value we read at the top-level
	# select. Two scheduler invocations both reading the same row will both attempt
	# the UPDATE; only one wins the WHERE clause (the loser sees attempt_count
	# already bumped). Closes the race where the previous read-then-check pattern
	# let two workers both pass `fresh_status == "Active"` and both fire WhatsApp.
	# Also subsumes the Paused-flip case (Script 12's cancel_retry_log) because
	# status != "Active" fails the WHERE.
	expected_attempt_count = int(row["attempt_count"] or 0)
	new_attempt_count = expected_attempt_count + 1
	frappe.db.sql(
		"""
		UPDATE `tabCRM Retry Log`
		SET attempt_count = %s, last_attempt_date = %s
		WHERE name = %s
		  AND status = 'Active'
		  AND attempt_count = %s
		""",
		(new_attempt_count, today(), log_name, expected_attempt_count),
	)
	affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	if not affected:
		summary["skipped"] += 1
		return

	active_status = cast(str, current["status"])
	active_lead_status = cast(str, current["lead_status"])

	frappe.enqueue(
		"crm.api.retry_engine.send_retry_whatsapp",
		queue="short",
		lead_name=lead_name,
		day=current_day,
	)

	next_day = RETRY_CADENCE.get(current_day)

	# Note: the claim above already set last_attempt_date and attempt_count.
	# Subsequent updates only touch day_in_sequence / next_attempt_date / status.
	if next_day is None:
		frappe.db.set_value(
			"CRM Retry Log",
			log_name,
			{
				"status": "Exhausted",
				"next_attempt_date": None,
			},
		)
		_move_lead_to_cold_after_exhaust(lead_name, active_lead_status, active_status)
		summary["exhausted"] += 1
		return

	gap_days = next_day - current_day
	frappe.db.set_value(
		"CRM Retry Log",
		log_name,
		{
			"day_in_sequence": next_day,
			"next_attempt_date": add_days(today(), gap_days),
		},
	)
	summary["advanced"] += 1


def _move_lead_to_cold_after_exhaust(lead_name: str, from_lead_status: str, current_status: str) -> None:
	"""Flip lead_status to Cold-Unresponsive via db.set_value to bypass ALLOWED_LEAD_STATUS_TRANSITIONS
	(Reactivated→Cold-Unresponsive is allowed but still tripped through Script 1 invariants) and avoid
	cascading the Lead After-Save script (no-op anyway since the retry log is already Exhausted).
	C-stage is preserved."""
	if from_lead_status == "Cold-Unresponsive":
		add_lead_comment(
			lead_name,
			"Retry sequence exhausted on Day 12 — lead remains Cold-Unresponsive.",
			source="SCHEDULER",
		)
		return

	frappe.db.set_value("CRM Lead", lead_name, "lead_status", "Cold-Unresponsive")
	add_lead_comment(
		lead_name,
		f"Retry sequence exhausted on Day 12 — auto-moved engagement {from_lead_status} → "
		f"Cold-Unresponsive (PRD §5.2). C-stage unchanged at {current_status}.",
		source="SCHEDULER",
	)

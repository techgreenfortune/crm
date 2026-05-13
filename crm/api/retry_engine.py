from __future__ import annotations

from typing import Any, cast

import frappe
from frappe.utils import add_days, today

from crm.api.call_log import add_lead_comment

RETRY_CADENCE: dict[int, int | None] = {1: 2, 2: 3, 3: 5, 5: 7, 7: 12, 12: None}
RETRY_ACTIVE_STATUSES: tuple[str, ...] = ("C0", "Cold", "Reactivated")


@frappe.whitelist()
def send_retry_whatsapp(lead_name: str, day: int) -> None:
	"""Send AiSensy follow-up message. Called via frappe.enqueue from Scheduler Event Server Script."""

	# Re-check: small window between scheduler enqueue and worker pickup
	current_status = frappe.db.get_value("CRM Lead", lead_name, "status")
	if current_status not in RETRY_ACTIVE_STATUSES:
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

	try:
		send_template_message(
			to=phone,
			template_name=template_name,
			variables=[lead.get("lead_name") or lead_name, lead_name, "Retry Call Follow-up"],
			reference_doctype="CRM Lead",
			reference_name=lead_name,
			recipient_name=lead.get("lead_name") or lead_name,
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
	summary: dict[str, int] = {"checked": 0, "advanced": 0, "exhausted": 0, "cancelled": 0, "errors": 0}

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

	current_status = frappe.db.get_value("CRM Lead", lead_name, "status")
	if current_status not in RETRY_ACTIVE_STATUSES:
		frappe.db.set_value(
			"CRM Retry Log",
			log_name,
			{"status": "Cancelled", "next_attempt_date": None},
		)
		summary["cancelled"] += 1
		return

	# Guard above proved current_status is a str from RETRY_ACTIVE_STATUSES;
	# cast strips the spurious `_dict` arm pyright can't subtract.
	active_status = cast(str, current_status)

	frappe.enqueue(
		"crm.api.retry_engine.send_retry_whatsapp",
		queue="short",
		lead_name=lead_name,
		day=current_day,
	)

	next_day = RETRY_CADENCE.get(current_day)
	new_attempt_count = (row["attempt_count"] or 0) + 1

	if next_day is None:
		frappe.db.set_value(
			"CRM Retry Log",
			log_name,
			{
				"status": "Exhausted",
				"next_attempt_date": None,
				"last_attempt_date": today(),
				"attempt_count": new_attempt_count,
			},
		)
		_move_lead_to_cold_after_exhaust(lead_name, active_status)
		summary["exhausted"] += 1
		return

	gap_days = next_day - current_day
	frappe.db.set_value(
		"CRM Retry Log",
		log_name,
		{
			"day_in_sequence": next_day,
			"last_attempt_date": today(),
			"next_attempt_date": add_days(today(), gap_days),
			"attempt_count": new_attempt_count,
		},
	)
	summary["advanced"] += 1


def _move_lead_to_cold_after_exhaust(lead_name: str, from_status: str) -> None:
	"""Move lead to Cold via db.set_value to bypass ALLOWED_TRANSITIONS (Reactivated→Cold
	is not in the map) and avoid cascading the Lead After-Save script (no-op anyway since
	the retry log is already Exhausted, not Active/Paused)."""
	if from_status == "Cold":
		add_lead_comment(lead_name, "Retry sequence exhausted on Day 12 — lead remains Cold.")
		return

	frappe.db.set_value("CRM Lead", lead_name, "status", "Cold")
	add_lead_comment(
		lead_name,
		f"Retry sequence exhausted on Day 12 — auto-moved {from_status} → Cold (PRD §5.2).",
	)

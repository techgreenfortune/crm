"""One-time backfill: push existing CRM Leads into the Meta Custom Audience.

Usage (via bench, not a web-exposed endpoint — this touches every lead and
makes real Graph API calls):

	bench --site <site> execute crm.integrations.meta.backfill.run_backfill
	bench --site <site> execute crm.integrations.meta.backfill.run_backfill --kwargs "{'dry_run': True}"

Skips any lead that already has a Won deal (``CRM Deal.lead`` pointing back
to it, with a status whose ``type`` is ``Won``) — those people belong in the
"already converted" bucket the Deal-Won hook removes them into, not the
fresh-lead audience.

Batches at ``batch_size`` rows per Graph API call (Meta's own cap is 10,000
rows/request — default here is smaller for reliability over a single HTTP
call; raise it up to 10,000 if you want fewer, bigger requests).
"""

from __future__ import annotations

import frappe

from crm.integrations.meta.audience import add_users_to_audience, build_user_row, get_settings

DEFAULT_BATCH_SIZE = 2000
MAX_BATCH_SIZE = 10000


def _won_lead_names() -> set[str]:
	won_statuses = frappe.get_all("CRM Deal Status", filters={"type": "Won"}, pluck="name")
	if not won_statuses:
		return set()
	leads = frappe.get_all(
		"CRM Deal",
		filters={"status": ["in", won_statuses], "lead": ["is", "set"]},
		pluck="lead",
	)
	return set(leads)


def _leads_to_sync() -> list[dict]:
	won = _won_lead_names()
	all_leads = frappe.get_all(
		"CRM Lead", fields=["name", "email", "mobile_no", "first_name", "last_name"]
	)
	return [lead for lead in all_leads if lead["name"] not in won]


def _chunks(items: list, size: int):
	for i in range(0, len(items), size):
		yield items[i : i + size]


def run_backfill(batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False) -> dict:
	"""Push every CRM Lead without a Won deal into the configured audience.

	Returns a summary dict; never raises — a batch failure is logged and
	skipped so the rest of the backfill still completes.
	"""
	batch_size = min(batch_size, MAX_BATCH_SIZE)

	if not dry_run and get_settings() is None:
		frappe.throw("CRM Meta Audience Settings is disabled or incomplete — nothing to backfill into.")

	leads = _leads_to_sync()
	summary = {
		"total_leads": frappe.db.count("CRM Lead"),
		"skipped_won": frappe.db.count("CRM Lead") - len(leads),
		"eligible": len(leads),
		"batches": 0,
		"synced": 0,
		"failed_batches": [],
	}

	for batch in _chunks(leads, batch_size):
		rows = [
			build_user_row(
				email=lead["email"],
				phone=lead["mobile_no"],
				first_name=lead["first_name"],
				last_name=lead["last_name"],
				country=None,
			)
			for lead in batch
		]
		summary["batches"] += 1
		if dry_run:
			summary["synced"] += len(rows)
			continue
		try:
			add_users_to_audience(rows)
			summary["synced"] += len(rows)
		except Exception:
			frappe.log_error(
				title=f"Meta Audience backfill batch failed ({len(rows)} leads)",
			)
			summary["failed_batches"].append([lead["name"] for lead in batch])

	return summary

"""One-time backfill: push existing CRM Leads into the Meta Custom Audience.

Usage
-----
Run as a background job (recommended — touches every lead and makes real
Graph API calls, so it shouldn't block a console/request):

	bench --site <site> execute crm.integrations.meta.backfill.enqueue_backfill

Or synchronously for a small site / a dry run:

	bench --site <site> execute crm.integrations.meta.backfill.run_backfill --kwargs "{'dry_run': True}"

Skips any lead that already has a Won deal (``CRM Deal.lead`` pointing back
to it, with a status whose ``type`` is ``Won``) — those people belong in the
"already converted" bucket the Deal-Won hook removes them into, not the
fresh-lead audience. Also skips any lead with neither an email nor a phone
number (a row with every field blank is unmatchable noise, not a real
candidate for the audience — same rule the live handlers apply).

Batches at ``batch_size`` rows per Graph API call (Meta's own cap is 10,000
rows/request — default here is smaller for reliability over a single HTTP
call; raise it up to 10,000 if you want fewer, bigger requests). Leads are
streamed from the database in pages rather than loaded all at once, so this
is safe to run against a lead table of any size.
"""

from __future__ import annotations

import itertools

import frappe

from crm.integrations.meta.audience import (
	add_users_to_audience,
	build_user_row,
	get_settings,
	has_matchable_contact,
	log_sync_failure,
)

DEFAULT_BATCH_SIZE = 2000
MAX_BATCH_SIZE = 10000
LEAD_PAGE_SIZE = 5000

BACKFILL_QUEUE = "long"


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


def _iter_all_leads(page_size: int = LEAD_PAGE_SIZE):
	"""Yield every CRM Lead one page at a time instead of loading the whole
	table into memory — safe regardless of how many leads exist."""
	start = 0
	while True:
		page = frappe.get_all(
			"CRM Lead",
			fields=["name", "email", "mobile_no", "first_name", "last_name"],
			order_by="name",
			limit_start=start,
			limit_page_length=page_size,
		)
		if not page:
			return
		yield from page
		start += page_size


def _has_contact_info(lead: dict) -> bool:
	return has_matchable_contact(email=lead.get("email"), phone=lead.get("mobile_no"))


def _leads_to_sync(summary: dict):
	"""Yield eligible leads (not Won, has contact info), tallying skip reasons into ``summary``."""
	won = _won_lead_names()
	for lead in _iter_all_leads():
		if lead["name"] in won:
			summary["skipped_won"] += 1
			continue
		if not _has_contact_info(lead):
			summary["skipped_no_contact"] += 1
			continue
		yield lead


def _chunks(iterable, size: int):
	"""Batch any iterable (list or generator) into lists of up to ``size`` items."""
	iterator = iter(iterable)
	while True:
		batch = list(itertools.islice(iterator, size))
		if not batch:
			return
		yield batch


def enqueue_backfill(batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False) -> None:
	"""Queue run_backfill on the long-running job queue instead of blocking
	the caller — the full backfill can take a while against a large lead
	table and real network calls to Meta."""
	frappe.enqueue(
		"crm.integrations.meta.backfill.run_backfill",
		queue=BACKFILL_QUEUE,
		batch_size=batch_size,
		dry_run=dry_run,
	)


def run_backfill(batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False) -> dict:
	"""Push every eligible CRM Lead into the configured audience.

	Returns a summary dict. Raises only up front if sync is disabled/
	incomplete (and not a dry run) — once batching starts, a single batch's
	failure is logged to Failed Meta Sync Log (one retryable row per lead in
	that batch) rather than raised, so the rest of the backfill still
	completes.
	"""
	batch_size = min(batch_size, MAX_BATCH_SIZE)

	if not dry_run and get_settings() is None:
		frappe.throw(
			frappe._("CRM Meta Audience Settings is disabled or incomplete — nothing to backfill into.")
		)

	summary = {
		"total_leads": frappe.db.count("CRM Lead"),
		"skipped_won": 0,
		"skipped_no_contact": 0,
		"batches": 0,
		"synced": 0,
		"failed": 0,
	}

	for batch in _chunks(_leads_to_sync(summary), batch_size):
		rows_by_lead = {
			lead["name"]: build_user_row(
				email=lead["email"],
				phone=lead["mobile_no"],
				first_name=lead["first_name"],
				last_name=lead["last_name"],
				country=None,
			)
			for lead in batch
		}
		summary["batches"] += 1
		if dry_run:
			summary["synced"] += len(rows_by_lead)
			continue
		try:
			add_users_to_audience(list(rows_by_lead.values()))
			summary["synced"] += len(rows_by_lead)
		except Exception:
			# Log each lead in the failed batch as its own retryable
			# Failed Meta Sync Log row, so a transient failure doesn't
			# require re-running the whole backfill — retry_sync() can
			# resend just the leads that actually failed.
			for lead_name, row in rows_by_lead.items():
				log_sync_failure("Add", "CRM Lead", lead_name, row)
			summary["failed"] += len(rows_by_lead)

	summary["eligible"] = summary["synced"] + summary["failed"]
	return summary

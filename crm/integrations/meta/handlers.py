"""Doc-event handlers wiring CRM Lead / CRM Deal into the Meta Custom Audience sync.

Wired from hooks.py:
	CRM Lead -> after_insert -> on_lead_created   (add to audience)
	CRM Deal -> on_update    -> on_deal_won       (remove from audience, Won only)

Both handlers only *enqueue* — the actual Graph API call runs in a background
worker (frappe.enqueue) so a slow/unavailable Meta API never blocks the save
that triggered it. Failures inside the job are caught and written to
``Failed Meta Sync Log`` rather than raised, since this runs outside any
request context by the time it fails.
"""

from __future__ import annotations

import frappe

from crm.integrations.meta.audience import add_users_to_audience, build_user_row, remove_users_from_audience

QUEUE = "short"


def _deal_status_type(status: str | None) -> str | None:
	if not status:
		return None
	return frappe.db.get_value("CRM Deal Status", status, "type")


def on_lead_created(doc, method=None):
	frappe.enqueue(
		"crm.integrations.meta.handlers.sync_add_lead",
		queue=QUEUE,
		enqueue_after_commit=True,
		lead_name=doc.name,
	)


def on_deal_won(doc, method=None):
	# Guard on an actual status change so re-saving an already-Won deal (e.g.
	# editing an unrelated field) doesn't re-fire the removal call every time.
	if not doc.has_value_changed("status"):
		return
	if _deal_status_type(doc.status) != "Won":
		return
	frappe.enqueue(
		"crm.integrations.meta.handlers.sync_remove_deal",
		queue=QUEUE,
		enqueue_after_commit=True,
		deal_name=doc.name,
	)


def _log_failure(sync_type: str, entity: str, entity_name: str, payload_row: list[str]):
	try:
		frappe.get_doc(
			{
				"doctype": "Failed Meta Sync Log",
				"sync_type": sync_type,
				"entity": entity,
				"entity_name": entity_name,
				"payload_row": frappe.as_json(payload_row),
				"traceback": frappe.get_traceback(),
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Logging the failure must never itself raise and swallow the original
		# error context — fall back to the generic error log.
		frappe.log_error(title=f"Failed to log Meta Audience sync failure ({sync_type}/{entity_name})")


def sync_add_lead(lead_name: str):
	lead = frappe.get_doc("CRM Lead", lead_name)
	row = build_user_row(
		email=lead.email,
		phone=lead.mobile_no,
		first_name=lead.first_name,
		last_name=lead.last_name,
		country=None,
	)
	try:
		add_users_to_audience([row])
	except Exception:
		_log_failure("Add", "CRM Lead", lead_name, row)
		frappe.log_error(title=f"Meta Audience add failed for CRM Lead {lead_name}")


def sync_remove_deal(deal_name: str):
	deal = frappe.get_doc("CRM Deal", deal_name)
	row = build_user_row(
		email=deal.email,
		phone=deal.mobile_no,
		first_name=deal.first_name,
		last_name=deal.last_name,
		country=None,
	)
	try:
		remove_users_from_audience([row])
	except Exception:
		_log_failure("Remove", "CRM Deal", deal_name, row)
		frappe.log_error(title=f"Meta Audience remove failed for CRM Deal {deal_name}")

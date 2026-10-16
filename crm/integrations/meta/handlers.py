"""Doc-event handlers wiring CRM Lead / CRM Deal into the Meta Custom Audience sync.

Wired from hooks.py:
	CRM Lead -> after_insert -> on_lead_created   (add to audience)
	CRM Deal -> on_update    -> on_deal_won       (remove from audience, Won only)

``sync_remove_lead`` is NOT wired via hooks.py at all — it's called directly
from ``crm.api.projects.create_project_for_lead``. That's the only place in
this fork where a Lead actually becomes "Won" (``lead_status`` flips via
``frappe.db.set_value``, which bypasses every Document event, including
``on_update``/``validate``), so a doc-event hook on CRM Lead would never fire
for this transition. CRM Deal's on_update hook is kept for the (separate,
generic Frappe CRM) Lead-to-Deal conversion flow, in case that path is ever
used independently of the Projects handoff.

Both handlers only *enqueue* — the actual Graph API call runs in a background
worker (frappe.enqueue) so a slow/unavailable Meta API never blocks the save
that triggered it. Failures inside the job are caught and written to
``Failed Meta Sync Log`` rather than raised, since this runs outside any
request context by the time it fails.
"""

from __future__ import annotations

import frappe

from crm.integrations.meta.audience import (
	add_users_to_audience,
	build_user_row,
	get_settings,
	has_matchable_contact,
	log_sync_failure,
	remove_users_from_audience,
)

QUEUE = "short"


def _deal_status_type(status: str | None) -> str | None:
	if not status:
		return None
	return frappe.db.get_value("CRM Deal Status", status, "type")


def on_lead_created(doc, method=None):
	# Skip enqueueing entirely when sync isn't configured — avoids piling up
	# useless background jobs for every single new lead before go-live.
	if get_settings() is None:
		return
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
	if get_settings() is None:
		return
	frappe.enqueue(
		"crm.integrations.meta.handlers.sync_remove_deal",
		queue=QUEUE,
		enqueue_after_commit=True,
		deal_name=doc.name,
	)


def sync_add_lead(lead_name: str):
	lead = frappe.get_doc("CRM Lead", lead_name)
	if not has_matchable_contact(email=lead.email, phone=lead.mobile_no):
		# No email and no phone — nothing Meta could ever match this row to;
		# sending it is pure noise against the audience's match rate.
		return
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
		log_sync_failure("Add", "CRM Lead", lead_name, row)


def sync_remove_deal(deal_name: str):
	deal = frappe.get_doc("CRM Deal", deal_name)
	# The Add call (sync_add_lead) hashes the *Lead's* fields. If this Remove
	# call hashes the Deal's own fields instead, and those differ from the
	# Lead's (e.g. the rep corrected the email/phone while working the deal),
	# the hashes won't match what's actually in the audience and the DELETE
	# silently removes nobody. Prefer the linked Lead's fields — same source
	# of truth the Add call used — falling back to the Deal's own fields only
	# when there's no linked Lead (e.g. the Deal was created directly).
	source = deal
	if deal.lead and frappe.db.exists("CRM Lead", deal.lead):
		source = frappe.get_doc("CRM Lead", deal.lead)
	if not has_matchable_contact(email=source.email, phone=source.mobile_no):
		return
	row = build_user_row(
		email=source.email,
		phone=source.mobile_no,
		first_name=source.first_name,
		last_name=source.last_name,
		country=None,
	)
	try:
		remove_users_from_audience([row])
	except Exception:
		log_sync_failure("Remove", "CRM Deal", deal_name, row)


def enqueue_remove_lead(lead_name: str):
	"""Enqueue the Meta audience removal for a Lead that just became Won.

	Call this directly from any code path that flips a Lead to Won without
	going through a Document save (e.g. ``frappe.db.set_value`` writes, which
	don't fire ``on_update``/``validate`` and so can't be caught by a
	doc-event hook). ``crm.api.projects.create_project_for_lead`` is the
	current such caller — it's the sole place leads become Won in this fork.
	No-ops if sync isn't configured, same as the other entry points.
	"""
	if get_settings() is None:
		return
	frappe.enqueue(
		"crm.integrations.meta.handlers.sync_remove_lead",
		queue=QUEUE,
		enqueue_after_commit=True,
		lead_name=lead_name,
	)


def sync_remove_lead(lead_name: str):
	lead = frappe.get_doc("CRM Lead", lead_name)
	if not has_matchable_contact(email=lead.email, phone=lead.mobile_no):
		return
	row = build_user_row(
		email=lead.email,
		phone=lead.mobile_no,
		first_name=lead.first_name,
		last_name=lead.last_name,
		country=None,
	)
	try:
		remove_users_from_audience([row])
	except Exception:
		log_sync_failure("Remove", "CRM Lead", lead_name, row)

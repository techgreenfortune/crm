"""CRM Task reminder triggers.

Two-path design:

* **Path A — immediate send on save** (``send_due_today_reminder``):
  doc_event hook (``after_insert`` + ``on_update``).  Fires the
  task_reminder Brevo email the moment a task is saved with
  ``due_date`` matching today and ``reminder_sent = 0``.

* **Path B — daily 9 PM cron** (``send_due_today_reminders_batch``):
  Catches tasks whose due_date arrives today but were created on
  earlier days (so no on-save event ever fired with due == today).

Both paths set ``reminder_sent = 1`` after dispatch to prevent
duplicates, and ``send_due_today_reminder`` clears the flag whenever
``due_date`` is changed so a future change can trigger a new reminder.
"""

from __future__ import annotations

import frappe

from crm.integrations.brevo.task_emails import fire_task_reminder

_TERMINAL_STATUSES = {"Done", "Canceled", "Cancelled"}


def _is_due_today(due_date) -> bool:
	"""``due_date`` is a Datetime field; compare on date part only."""
	if not due_date:
		return False
	return frappe.utils.getdate(due_date) == frappe.utils.getdate()


def _is_active(task_doc) -> bool:
	status = (task_doc.get("status") or "").strip()
	return status not in _TERMINAL_STATUSES


def send_due_today_reminder(doc, method=None):
	"""doc_event handler — runs on every save of a CRM Task.

	Two side-effects:

	1. If ``due_date`` has changed since the previous save, clear
	   ``reminder_sent`` so a new reminder can fire on the new date.
	2. If ``due_date`` is today, the task is active, and the reminder
	   hasn't been sent yet — enqueue the reminder email and flip
	   ``reminder_sent = 1``.
	"""
	try:
		# (1) Detect due-date change and reset the flag so the new date triggers.
		# has_value_changed returns True on insert too; guard against that with
		# an explicit previous-doc lookup.
		old = doc.get_doc_before_save()
		if old and old.get("due_date") != doc.get("due_date"):
			if doc.get("reminder_sent"):
				doc.db_set("reminder_sent", 0, update_modified=False)
				# Refresh the in-memory value so the next branch sees the reset.
				doc.reminder_sent = 0

		# (2) Immediate send when due_date is today.
		if _is_due_today(doc.get("due_date")) and not doc.get("reminder_sent") and _is_active(doc):
			fire_task_reminder(doc.name)
			doc.db_set("reminder_sent", 1, update_modified=False)
	except Exception:
		# Never let reminder logic break the save.  Real failures bubble up
		# inside the queued worker; here we just guard the doc_event path.
		frappe.log_error(
			title="Task reminder on-save handler failed",
			message=frappe.get_traceback(),
		)


def send_due_today_reminders_batch():
	"""Cron — fires task_reminder for all tasks due today with no prior send.

	Walks the CRM Task table for tasks whose ``due_date`` (date part only)
	matches today, ``reminder_sent`` is 0 (or NULL — covers rows that
	pre-date the field), and an active status; enqueues an email for each
	and sets ``reminder_sent = 1`` to prevent re-fires.

	Uses raw SQL (``DATE(due_date) = ?``) so the time component is
	ignored — Frappe's filter language doesn't natively wrap fields in
	``DATE()``.  ``COALESCE(reminder_sent, 0)`` treats legacy NULLs as 0
	so a fresh migration doesn't silently exclude pre-existing tasks.
	"""
	today = frappe.utils.getdate()
	candidates = frappe.db.sql(
		"""
		SELECT name FROM `tabCRM Task`
		WHERE DATE(due_date) = %s
		  AND COALESCE(reminder_sent, 0) = 0
		  AND status NOT IN ('Done', 'Canceled', 'Cancelled')
		""",
		(today,),
		pluck=True,
	)
	for task_name in candidates:
		try:
			fire_task_reminder(task_name)
			frappe.db.set_value("CRM Task", task_name, "reminder_sent", 1, update_modified=False)
		except Exception:
			frappe.log_error(
				title="Task reminder batch — failed for one task",
				message=f"task={task_name}\n{frappe.get_traceback()}",
			)

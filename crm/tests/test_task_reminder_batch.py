from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from crm.api.tasks import send_due_today_reminders_batch


class TestTaskReminderBatch(FrappeTestCase):
	def _task(self, status="Todo"):
		task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Reminder batch test",
				"status": status,
				"due_date": add_days(now_datetime(), 1),
			}
		).insert(ignore_permissions=True)
		# Moved to today behind the on-save hook's back, as happens when a future due date arrives.
		task.db_set("due_date", now_datetime(), update_modified=False)
		return task.name

	def test_batch_is_scheduled(self):
		crons = frappe.get_hooks("scheduler_events").get("cron", {})
		self.assertIn("crm.api.tasks.send_due_today_reminders_batch", crons.get("0 9 * * *", []))

	def test_reminds_open_task_due_today_once(self):
		task = self._task()
		with patch("crm.api.tasks.fire_task_reminder") as fire:
			send_due_today_reminders_batch()
			send_due_today_reminders_batch()
		self.assertEqual([c.args[0] for c in fire.call_args_list].count(task), 1)
		self.assertEqual(frappe.db.get_value("CRM Task", task, "reminder_sent"), 1)

	def test_skips_done_task(self):
		task = self._task(status="Done")
		with patch("crm.api.tasks.fire_task_reminder") as fire:
			send_due_today_reminders_batch()
		self.assertNotIn(task, [c.args[0] for c in fire.call_args_list])

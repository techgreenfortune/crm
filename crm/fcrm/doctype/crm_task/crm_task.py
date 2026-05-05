# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.form.assign_to import add as assign
from frappe.desk.form.assign_to import remove as unassign
from frappe.model.document import Document

POOL_TASK_ROLES = {
	"Call Lead — ": "Calling Team",
	"Upload Quote — ": "Estimation Team",
	"Handle Fabricator Lead — ": "B2F Team",
}


class CRMTask(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assigned_to: DF.Link | None
		description: DF.TextEditor | None
		due_date: DF.Datetime | None
		name: DF.Int | None
		priority: DF.Literal["Low", "Medium", "High"]
		reference_docname: DF.DynamicLink | None
		reference_doctype: DF.Link | None
		start_date: DF.Date | None
		status: DF.Literal["Backlog", "Todo", "In Progress", "Done", "Canceled"]
		title: DF.Data
	# end: auto-generated types

	def after_insert(self):
		self.assign_to()

	def validate(self):
		self._check_write_permission()
		if self.is_new() or not self.assigned_to:
			return

		if self.get_doc_before_save().assigned_to != self.assigned_to:
			self.unassign_from_previous_user(self.get_doc_before_save().assigned_to)
			self.assign_to()

	def on_trash(self):
		self._check_write_permission(is_delete=True)

	def _check_write_permission(self, is_delete=False):
		user = frappe.session.user
		if user == "Administrator" or self.flags.get("ignore_permissions"):
			return

		user_roles = set(frappe.get_roles(user))
		if "System Manager" in user_roles or "Sales Manager" in user_roles:
			return

		title = self.title or ""

		for prefix, required_role in POOL_TASK_ROLES.items():
			if title.startswith(prefix):
				if is_delete:
					frappe.throw(
						_("Pool tasks cannot be deleted. Mark the task as Done or Canceled instead."),
						frappe.PermissionError,
						title=_("Not Permitted"),
					)
				if required_role in user_roles or self.assigned_to == user:
					return
				frappe.throw(
					_(f"Only {required_role} members can update this task."),
					frappe.PermissionError,
					title=_("Not Permitted"),
				)

		# Non-pool tasks: must be the assignee or the lead owner
		if self.assigned_to and self.assigned_to != user:
			lead_owner = None
			if self.reference_doctype == "CRM Lead" and self.reference_docname:
				lead_owner = frappe.db.get_value("CRM Lead", self.reference_docname, "lead_owner")
			if lead_owner != user:
				frappe.throw(
					_("You can only update tasks assigned to you or for leads you own."),
					frappe.PermissionError,
					title=_("Not Permitted"),
				)

	def unassign_from_previous_user(self, user: str | None):
		if user:
			unassign(self.doctype, self.name, user)

	def assign_to(self):
		if self.assigned_to:
			assign(
				{
					"assign_to": [self.assigned_to],
					"doctype": self.doctype,
					"name": self.name,
					"description": self.title or self.description,
				}
			)

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Title",
				"type": "Data",
				"key": "title",
				"width": "16rem",
			},
			{
				"label": "Status",
				"type": "Select",
				"key": "status",
				"width": "8rem",
			},
			{
				"label": "Priority",
				"type": "Select",
				"key": "priority",
				"width": "8rem",
			},
			{
				"label": "Due Date",
				"type": "Date",
				"key": "due_date",
				"width": "8rem",
			},
			{
				"label": "Assigned To",
				"type": "Link",
				"key": "assigned_to",
				"width": "10rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "8rem",
			},
		]

		rows = [
			"name",
			"title",
			"description",
			"assigned_to",
			"due_date",
			"status",
			"priority",
			"reference_doctype",
			"reference_docname",
			"modified",
		]
		return {"columns": columns, "rows": rows}

	@staticmethod
	def default_kanban_settings():
		return {
			"column_field": "status",
			"title_field": "title",
			"kanban_fields": '["description", "priority", "creation"]',
		}

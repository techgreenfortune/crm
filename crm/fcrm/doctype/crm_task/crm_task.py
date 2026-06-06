# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.desk.form.assign_to import add as assign
from frappe.desk.form.assign_to import remove as unassign
from frappe.model.document import Document

from crm.permissions.role_config import TIER1_FULL_RW

# Pool task types and the role that owns each pool. Single source of truth.
# Used by:
#   - validate_write_permission       (before_save doc_event)
#   - _check_delete_permission        (called from CRMTask.on_trash)
#   - get_permission_query_conditions (list filter hook)
#   - crm/api/activities.py           (activity-panel writers)
# All wired in hooks.py.
POOL_TASK_ROLES = {
	"call_lead": "Calling Team",
	"upload_quote": "Estimation Team",
	"handle_fabricator_lead": "B2F Team",
}


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List-level filter for CRM Task.

	Wired via ``permission_query_conditions["CRM Task"]`` in hooks.py. Returns
	a SQL WHERE clause (or empty string for "no filter").

	Rules:
	- ``Administrator`` and privileged-task roles (``TIER1_FULL_RW``):
	  see everything.
	- Everyone else:
	  - Tasks assigned to them (``assigned_to``), OR
	  - Tasks whose parent ``CRM Lead`` they can see under the pure-hierarchy
	    rule (lead_owner = user, or owned by a subtree member for ASM/RSM).
	  - If the user holds any pool roles (Calling Team / Estimation Team /
	    B2F Team), they additionally see unassigned tasks of that pool's
	    task_type. A multi-pool user sees the union of all their pools.

	Replaces the "CRM Task — Permission Query" Server Script on 2026-05-22.
	The role→task_type mapping is derived from POOL_TASK_ROLES so adding a new
	pool task type only requires updating the one dict. Multi-assignee
	(``_assign``) was retired — task/lead access follows the single owner field.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return ""

	esc = frappe.db.escape
	escaped_user = esc(user)

	# Parent-lead owners the user may see: themselves + (for ASM/RSM) their
	# CRM Sales Hierarchy subtree — mirrors CRM Lead visibility.
	owners = {user}
	if roles & {"ASM", "RSM"}:
		from crm.overrides.crm_lead_permissions import downstream_users

		owners |= downstream_users(user)
	owners_in = ", ".join(esc(u) for u in sorted(owners))

	base = f"""(
		`tabCRM Task`.assigned_to = {escaped_user}
		OR (
			`tabCRM Task`.reference_doctype = 'CRM Lead'
			AND EXISTS (
				SELECT 1 FROM `tabCRM Lead`
				WHERE `tabCRM Lead`.name = `tabCRM Task`.reference_docname
				AND `tabCRM Lead`.lead_owner IN ({owners_in})
			)
		)
	)"""

	# Union of every pool the user belongs to (multi-pool users see all their
	# pools, not just the first match — fix for the old elif chain).
	pool_task_types = sorted(tt for tt, role in POOL_TASK_ROLES.items() if role in roles)
	pool_clauses = [
		f"((`tabCRM Task`.assigned_to IS NULL OR `tabCRM Task`.assigned_to = '') AND `tabCRM Task`.task_type = '{tt}')"
		for tt in pool_task_types
	]

	if pool_clauses:
		return "(" + base + " OR " + " OR ".join(pool_clauses) + ")"
	return base


def validate_write_permission(doc, method=None):
	"""before_save gate for CRM Task. Combines:

	1. Won-lead lock: any task save against a C4 + lead_status=='Won' parent
	   throws (Administrator / System Manager / `flags.ignore_c4_lock` bypass).
	2. task_type immutability: once set, the type cannot change (Administrator
	   / privileged-task roles bypass — see ``TIER1_FULL_RW``).
	3. Pool/assignee permission: pool tasks need the matching role; non-pool
	   tasks need the assignee or the parent lead owner.

	Ported from the "CRM Task — Validate — Write Permission" Server Script on
	2026-05-22 to eliminate POOL_TASK_ROLES duplication. See
	[admin-ui-setup-guide.md §17.5] for context.
	"""
	user = frappe.session.user
	if user == "Administrator" or doc.flags.get("ignore_permissions"):
		return

	# Won-lead lock (parent at C4 + Won).
	if doc.reference_doctype == "CRM Lead" and doc.reference_docname:
		parent_state = frappe.db.get_value(
			"CRM Lead",
			doc.reference_docname,
			["status", "lead_status"],
			as_dict=True,
		)
		if parent_state and parent_state.status == "C4" and parent_state.lead_status == "Won":
			roles = set(frappe.get_roles(user))
			if "System Manager" not in roles and not doc.flags.get("ignore_c4_lock"):
				frappe.throw(
					frappe._("Parent lead is Won (C4 + Won). Tasks against this lead are locked."),
					title=frappe._("Lead Archived"),
				)

	# task_type immutability.
	old_doc = doc.get_doc_before_save()
	if old_doc and old_doc.task_type and doc.task_type != old_doc.task_type:
		roles = set(frappe.get_roles(user))
		if not (roles & TIER1_FULL_RW):
			frappe.throw(
				frappe._(
					"task_type cannot be changed after the task is created (was {0!r}, attempted {1!r})."
				).format(old_doc.task_type, doc.task_type),
				frappe.PermissionError,
				title=frappe._("Task Type Locked"),
			)

	# Pool / assignee permission.
	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return

	task_type = doc.task_type or ""
	if task_type in POOL_TASK_ROLES:
		required_role = POOL_TASK_ROLES[task_type]
		if required_role not in roles and doc.assigned_to != user:
			frappe.throw(
				frappe._("Only {0} members can update this task.").format(required_role),
				frappe.PermissionError,
				title=frappe._("Not Permitted"),
			)
	elif doc.assigned_to and doc.assigned_to != user:
		lead_owner = None
		if doc.reference_doctype == "CRM Lead" and doc.reference_docname:
			lead_owner = frappe.db.get_value("CRM Lead", doc.reference_docname, "lead_owner")
		if lead_owner != user:
			frappe.throw(
				frappe._("You can only update tasks assigned to you or for leads you own."),
				frappe.PermissionError,
				title=frappe._("Not Permitted"),
			)


def _check_delete_permission(doc):
	"""Delete gate for CRM Task, called from ``CRMTask.on_trash``. Two rules:

	1. Pool tasks cannot be deleted at all — they must be marked Done or
	   Canceled instead. Reason: pool tasks track the work queue for a role;
	   deleting one breaks the audit trail.
	2. Non-pool tasks: only the assignee or the parent lead owner can delete
	   (Administrator / privileged-task roles bypass — see
	   ``TIER1_FULL_RW``).

	Lives as a method-call inside ``on_trash`` (not a doc_event) because the
	class already owns ``on_trash`` for the CRM Notification cascade — placing
	this check at the top of that method runs it before the cascade deletes.
	Ported from the "CRM Task — Before Delete — Delete Permission" Server
	Script on 2026-05-22.
	"""
	user = frappe.session.user
	if user == "Administrator" or doc.flags.get("ignore_permissions"):
		return

	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return

	if (doc.task_type or "") in POOL_TASK_ROLES:
		frappe.throw(
			frappe._("Pool tasks cannot be deleted. Mark the task as Done or Canceled instead."),
			frappe.PermissionError,
			title=frappe._("Not Permitted"),
		)

	if doc.assigned_to and doc.assigned_to != user:
		lead_owner = None
		if doc.reference_doctype == "CRM Lead" and doc.reference_docname:
			lead_owner = frappe.db.get_value("CRM Lead", doc.reference_docname, "lead_owner")
		if lead_owner != user:
			frappe.throw(
				frappe._("You can only delete tasks assigned to you or for leads you own."),
				frappe.PermissionError,
				title=frappe._("Not Permitted"),
			)


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
		task_type: DF.Literal["", "call_lead", "upload_quote", "handle_fabricator_lead", "review_quote"]
		title: DF.Data
	# end: auto-generated types

	def on_trash(self):
		_check_delete_permission(self)
		frappe.db.delete(
			"CRM Notification",
			{
				"reference_doctype": "CRM Task",
				"reference_name": self.name,
			},
		)
		frappe.db.delete(
			"CRM Notification",
			{
				"notification_type_doctype": "CRM Task",
				"notification_type_doc": self.name,
			},
		)

	def after_insert(self):
		self.assign_to()

	def after_save(self):
		self._record_pool_claim_trail()

	def _record_pool_claim_trail(self):
		if self.is_new():
			return
		old = self.get_doc_before_save()
		old_assignee = old.assigned_to if old else None
		new_assignee = self.assigned_to

		if (
			self.task_type == "upload_quote"
			and not old_assignee
			and new_assignee
			and self.reference_doctype == "CRM Lead"
		):
			try:
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "CRM Lead",
						"reference_name": self.reference_docname,
						"content": f"[AUTOMATION] Upload Quote task claimed by {new_assignee}.",
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass

	def validate(self):
		if self.is_new() or not self.assigned_to:
			return

		if self.get_doc_before_save().assigned_to != self.assigned_to:
			self.unassign_from_previous_user(self.get_doc_before_save().assigned_to)
			self.assign_to()

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
			"task_type",
			"title",
			"description",
			"assigned_to",
			"due_date",
			"status",
			"priority",
			"reference_doctype",
			"reference_docname",
			"quote_request",
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

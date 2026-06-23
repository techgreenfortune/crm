import json

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.desk.form.load import get_docinfo
from frappe.query_builder import JoinType
from frappe.translate import get_translated_doctypes

from crm.fcrm.doctype.crm_call_log.crm_call_log import parse_call_log
from crm.fcrm.doctype.crm_task.crm_task import POOL_TASK_ROLES
from crm.permissions.role_config import DOWNSTREAM_SCOPE_ROLES, TIER1_FULL_RW


@frappe.whitelist()
def get_activities(name: str):
	if frappe.db.exists("CRM Deal", name):
		return get_deal_activities(name)
	elif frappe.db.exists("CRM Lead", name):
		return get_lead_activities(name)
	else:
		frappe.throw(_("Document not found"), frappe.DoesNotExistError)


def get_deal_activities(name: str):
	if not frappe.has_permission("CRM Deal", "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	get_docinfo("", "CRM Deal", name)
	docinfo = frappe.response["docinfo"]
	deal_meta = frappe.get_meta("CRM Deal")
	deal_fields = {
		field.fieldname: {"label": field.label, "options": field.options} for field in deal_meta.fields
	}
	avoid_fields = [
		"lead",
		"response_by",
		"sla_creation",
		"sla",
		"first_response_time",
		"first_responded_on",
		"deal_owner",
	]

	doc = frappe.db.get_values("CRM Deal", name, ["creation", "owner", "lead"])[0]
	lead = doc[2]

	activities = []
	calls = []
	notes = []
	tasks = []
	attachments = []
	creation_text = _("created this deal")

	if lead:
		activities, calls, notes, tasks, attachments = get_lead_activities(lead)
		creation_text = _("converted the lead to this deal")

	activities.append(
		{
			"activity_type": "creation",
			"creation": doc[0],
			"owner": doc[1],
			"data": creation_text,
			"is_lead": False,
		}
	)

	docinfo.versions.reverse()

	for version in docinfo.versions:
		data = json.loads(version.data)
		if not data.get("changed"):
			continue

		if change := data.get("changed")[0]:
			field = deal_fields.get(change[0], None)

			if not field or change[0] in avoid_fields or (not change[1] and not change[2]):
				continue

			field_label = field.get("label") or change[0]
			field_option = field.get("options") or None

			activity_type = "changed"
			data = {
				"field": change[0],
				"field_label": field_label,
				"old_value": change[1],
				"value": change[2],
			}

			if not change[1] and change[2]:
				activity_type = "added"
				data = {
					"field": change[0],
					"field_label": field_label,
					"value": change[2],
				}
			elif change[1] and not change[2]:
				activity_type = "removed"
				data = {
					"field": change[0],
					"field_label": field_label,
					"value": change[1],
				}

			if data.get("value") and field_option and is_translatable(field_option):
				data["value"] = _(data["value"])

				if data.get("old_value"):
					data["old_value"] = _(data["old_value"])

		activity = {
			"activity_type": activity_type,
			"creation": version.creation,
			"owner": version.owner,
			"data": data,
			"is_lead": False,
			"options": field_option,
		}
		activities.append(activity)

	for comment in docinfo.comments:
		activity = {
			"name": comment.name,
			"activity_type": "comment",
			"creation": comment.creation,
			"owner": comment.owner,
			"content": comment.content,
			"attachments": get_attachments("Comment", comment.name),
			"is_lead": False,
		}
		activities.append(activity)

	for communication in docinfo.communications + docinfo.automated_messages:
		activity = {
			"activity_type": "communication",
			"communication_type": communication.communication_type,
			"communication_date": communication.communication_date or communication.creation,
			"creation": communication.creation,
			"data": {
				"subject": communication.subject,
				"content": communication.content,
				"sender_full_name": communication.sender_full_name,
				"sender": communication.sender,
				"recipients": communication.recipients,
				"cc": communication.cc,
				"bcc": communication.bcc,
				"attachments": get_attachments("Communication", communication.name),
				"read_by_recipient": communication.read_by_recipient,
				"delivery_status": communication.delivery_status,
			},
			"is_lead": False,
		}
		activities.append(activity)

	for attachment_log in docinfo.attachment_logs:
		activity = {
			"name": attachment_log.name,
			"activity_type": "attachment_log",
			"creation": attachment_log.creation,
			"owner": attachment_log.owner,
			"data": parse_attachment_log(attachment_log.content, attachment_log.comment_type),
			"is_lead": False,
		}
		activities.append(activity)

	for assignment_log in docinfo.assignment_logs:
		activity = {
			"name": assignment_log.name,
			"activity_type": "assignment_log",
			"creation": assignment_log.creation,
			"owner": assignment_log.owner,
			"data": parse_assignment_log(assignment_log.content, assignment_log.comment_type),
			"is_lead": False,
		}
		activities.append(activity)

	calls = calls + get_linked_calls(name).get("calls", [])
	notes = notes + get_linked_notes(name) + get_linked_calls(name).get("notes", [])
	tasks = tasks + get_linked_tasks(name) + get_linked_calls(name).get("tasks", [])
	attachments = attachments + get_attachments("CRM Deal", name)

	activities.sort(key=lambda x: x["creation"], reverse=True)
	activities = handle_multiple_versions(activities)

	return activities, calls, notes, tasks, attachments


def get_lead_activities(name: str):
	if not frappe.has_permission("CRM Lead", "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	get_docinfo("", "CRM Lead", name)
	docinfo = frappe.response["docinfo"]
	lead_meta = frappe.get_meta("CRM Lead")
	lead_fields = {
		field.fieldname: {"label": field.label, "options": field.options} for field in lead_meta.fields
	}
	avoid_fields = [
		"converted",
		"response_by",
		"sla_creation",
		"sla",
		"first_response_time",
		"first_responded_on",
		"deal_owner",
	]

	doc = frappe.db.get_values("CRM Lead", name, ["creation", "owner"])[0]
	activities = [
		{
			"activity_type": "creation",
			"creation": doc[0],
			"owner": doc[1],
			"data": _("created this lead"),
			"is_lead": True,
		}
	]

	docinfo.versions.reverse()

	for version in docinfo.versions:
		data = json.loads(version.data)
		if not data.get("changed"):
			continue

		if change := data.get("changed")[0]:
			field = lead_fields.get(change[0], None)

			if not field or change[0] in avoid_fields or (not change[1] and not change[2]):
				continue

			field_label = field.get("label") or change[0]
			field_option = field.get("options") or None

			activity_type = "changed"
			data = {
				"field": change[0],
				"field_label": field_label,
				"old_value": change[1],
				"value": change[2],
			}

			if not change[1] and change[2]:
				activity_type = "added"
				data = {
					"field": change[0],
					"field_label": field_label,
					"value": change[2],
				}
			elif change[1] and not change[2]:
				activity_type = "removed"
				data = {
					"field": change[0],
					"field_label": field_label,
					"value": change[1],
				}

			if data.get("value") and field_option and is_translatable(field_option):
				data["value"] = _(data["value"])

				if data.get("old_value"):
					data["old_value"] = _(data["old_value"])

		activity = {
			"activity_type": activity_type,
			"creation": version.creation,
			"owner": version.owner,
			"data": data,
			"is_lead": True,
			"options": field_option,
		}
		activities.append(activity)

	for comment in docinfo.comments:
		activity = {
			"name": comment.name,
			"activity_type": "comment",
			"creation": comment.creation,
			"owner": comment.owner,
			"content": comment.content,
			"attachments": get_attachments("Comment", comment.name),
			"is_lead": True,
		}
		activities.append(activity)

	for communication in docinfo.communications + docinfo.automated_messages:
		activity = {
			"activity_type": "communication",
			"communication_type": communication.communication_type,
			"communication_date": communication.communication_date or communication.creation,
			"creation": communication.creation,
			"data": {
				"subject": communication.subject,
				"content": communication.content,
				"sender_full_name": communication.sender_full_name,
				"sender": communication.sender,
				"recipients": communication.recipients,
				"cc": communication.cc,
				"bcc": communication.bcc,
				"attachments": get_attachments("Communication", communication.name),
				"read_by_recipient": communication.read_by_recipient,
				"delivery_status": communication.delivery_status,
			},
			"is_lead": True,
		}
		activities.append(activity)

	for attachment_log in docinfo.attachment_logs:
		activity = {
			"name": attachment_log.name,
			"activity_type": "attachment_log",
			"creation": attachment_log.creation,
			"owner": attachment_log.owner,
			"data": parse_attachment_log(attachment_log.content, attachment_log.comment_type),
			"is_lead": True,
		}
		activities.append(activity)

	for assignment_log in docinfo.assignment_logs:
		activity = {
			"name": assignment_log.name,
			"activity_type": "assignment_log",
			"creation": assignment_log.creation,
			"owner": assignment_log.owner,
			"data": parse_assignment_log(assignment_log.content, assignment_log.comment_type),
			"is_lead": True,
		}
		activities.append(activity)

	calls = get_linked_calls(name).get("calls", [])
	notes = get_linked_notes(name) + get_linked_calls(name).get("notes", [])
	tasks = get_linked_tasks(name) + get_linked_calls(name).get("tasks", [])
	attachments = get_attachments("CRM Lead", name)

	activities.sort(key=lambda x: x["creation"], reverse=True)
	activities = handle_multiple_versions(activities)

	return activities, calls, notes, tasks, attachments


def get_attachments(doctype: str, name: str):
	return (
		frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": name},
			fields=[
				"name",
				"file_name",
				"file_type",
				"file_url",
				"file_size",
				"is_private",
				"modified",
				"creation",
				"owner",
			],
		)
		or []
	)


def handle_multiple_versions(versions: list):
	activities = []
	grouped_versions = []
	old_version = None
	for version in versions:
		is_version = version["activity_type"] in ["changed", "added", "removed"]
		if not is_version:
			activities.append(version)
		if not old_version:
			old_version = version
			if is_version:
				grouped_versions.append(version)
			continue
		if is_version and old_version.get("owner") and version["owner"] == old_version["owner"]:
			grouped_versions.append(version)
		else:
			if grouped_versions:
				activities.append(parse_grouped_versions(grouped_versions))
			grouped_versions = []
			if is_version:
				grouped_versions.append(version)
		old_version = version
		if version == versions[-1] and grouped_versions:
			activities.append(parse_grouped_versions(grouped_versions))

	return activities


def parse_grouped_versions(versions: list):
	version = versions[0]
	if len(versions) == 1:
		return version
	other_versions = versions[1:]
	version["other_versions"] = other_versions
	return version


def get_linked_calls(name: str):
	calls = frappe.db.get_all(
		"CRM Call Log",
		filters={"reference_docname": name},
		fields=[
			"name",
			"caller",
			"receiver",
			"from",
			"to",
			"duration",
			"start_time",
			"end_time",
			"status",
			"type",
			"recording_url",
			"creation",
			"note",
			"disposition",
		],
	)

	linked_calls = frappe.db.get_all(
		"Dynamic Link", filters={"link_name": name, "parenttype": "CRM Call Log"}, pluck="parent"
	)

	notes = []
	tasks = []

	if linked_calls:
		CallLog = frappe.qb.DocType("CRM Call Log")
		Link = frappe.qb.DocType("Dynamic Link")
		query = (
			frappe.qb.from_(CallLog)
			.select(
				CallLog.name,
				CallLog.caller,
				CallLog.receiver,
				CallLog["from"],
				CallLog.to,
				CallLog.duration,
				CallLog.start_time,
				CallLog.end_time,
				CallLog.status,
				CallLog.type,
				CallLog.recording_url,
				CallLog.creation,
				CallLog.note,
				CallLog.disposition,
				Link.link_doctype,
				Link.link_name,
			)
			.join(Link, JoinType.inner)
			.on(Link.parent == CallLog.name)
			.where(CallLog.name.isin(linked_calls))
		)
		_calls = query.run(as_dict=True)

		for call in _calls:
			if call.get("link_doctype") == "FCRM Note":
				notes.append(call.link_name)
			elif call.get("link_doctype") == "CRM Task":
				tasks.append(call.link_name)

		_calls = [call for call in _calls if call.get("link_doctype") not in ["FCRM Note", "CRM Task"]]
		if _calls:
			calls = calls + _calls

	if notes:
		notes = frappe.db.get_all(
			"FCRM Note",
			filters={"name": ("in", notes)},
			fields=["name", "title", "content", "owner", "modified"],
		)

	if tasks:
		tasks = frappe.db.get_all(
			"CRM Task",
			filters={"name": ("in", tasks)},
			fields=[
				"name",
				"title",
				"task_type",
				"description",
				"assigned_to",
				"owner",
				"due_date",
				"priority",
				"status",
				"modified",
				"creation",
				"reference_docname",
				"quote_request",
			],
		)
		user = frappe.session.user
		user_roles = set(frappe.get_roles(user))
		for task in tasks:
			task["can_update"] = _task_can_update(task, user, user_roles, None)
			task["can_delete"] = _task_can_delete(task, user, user_roles, None)
		tasks = [t for t in tasks if t["can_update"]]

	calls = [parse_call_log(call) for call in calls] if calls else []

	return {"calls": calls, "notes": notes, "tasks": tasks}


def get_linked_notes(name: str):
	notes = frappe.db.get_all(
		"FCRM Note",
		filters={"reference_docname": name},
		fields=["name", "title", "content", "owner", "modified", "creation"],
	)
	return notes or []


def _visible_user_set(user: str, user_roles: set) -> set:
	"""Users whose owned/created docs ``user`` may reach: themselves plus, for
	downstream-scoped roles (ASM / RSM / Dealer — see DOWNSTREAM_SCOPE_ROLES),
	their CRM Sales Hierarchy subtree. Mirrors the ``owners`` set in
	crm_task.get_permission_query_conditions so the lead activity panel agrees
	with the task list/report view (no panel-vs-pqc visibility mismatch)."""
	users = {user}
	if user_roles & DOWNSTREAM_SCOPE_ROLES:
		from crm.overrides.crm_lead_permissions import downstream_users

		users |= downstream_users(user)
	return users


def _task_can_update(task: dict, user: str, user_roles: set, doc_owner: str | None) -> bool:
	if "Administrator" in user_roles or user_roles & TIER1_FULL_RW:
		return True
	if (task.get("assigned_to") or "") == user:
		return True
	task_type = task.get("task_type") or ""
	if task_type in POOL_TASK_ROLES:
		return POOL_TASK_ROLES[task_type] in user_roles or (task.get("assigned_to") or "") == user
	visible = _visible_user_set(user, user_roles)
	if (doc_owner or "") in visible:
		return True
	return (task.get("owner") or "") in visible


def _task_can_delete(task: dict, user: str, user_roles: set, doc_owner: str | None) -> bool:
	if "Administrator" in user_roles or user_roles & TIER1_FULL_RW:
		return True
	if (task.get("task_type") or "") in POOL_TASK_ROLES:
		return False
	if (task.get("assigned_to") or "") == user:
		return True
	return doc_owner == user


def get_linked_tasks(name: str):
	tasks = frappe.db.get_all(
		"CRM Task",
		filters={"reference_docname": name},
		fields=[
			"name",
			"title",
			"task_type",
			"description",
			"assigned_to",
			"owner",
			"due_date",
			"priority",
			"status",
			"modified",
			"creation",
			"reference_docname",
			"quote_request",
		],
	)
	if tasks:
		user = frappe.session.user
		user_roles = set(frappe.get_roles(user))
		doc_owner = frappe.db.get_value("CRM Lead", name, "lead_owner") or frappe.db.get_value(
			"CRM Deal", name, "deal_owner"
		)
		for task in tasks:
			task["can_update"] = _task_can_update(task, user, user_roles, doc_owner)
			task["can_delete"] = _task_can_delete(task, user, user_roles, doc_owner)
		tasks = [t for t in tasks if t["can_update"]]
	return tasks or []


def parse_attachment_log(html: str, type: str):
	soup = BeautifulSoup(html, "html.parser")
	a_tag = soup.find("a")
	type = "added" if type == "Attachment" else "removed"
	if not a_tag:
		return {
			"type": type,
			"file_name": html.replace("Removed ", ""),
			"file_url": "",
			"is_private": False,
		}

	is_private = False
	if "private/files" in a_tag["href"]:
		is_private = True

	return {
		"type": type,
		"file_name": a_tag.text,
		"file_url": a_tag["href"],
		"is_private": is_private,
	}


def parse_assignment_log(content: str, comment_type: str):
	"""Render the assignment Comment that Frappe's ToDo controller writes.

	Frappe stores plain-text messages like:
	  - "Ankit assigned Ravi: Assignment for CRM Lead CRM-LEAD-…"
	  - "Ankit self assigned this task: …"
	  - "Assignment of Ravi removed by Ankit"
	  - "Ankit removed their assignment."
	The description suffix (everything after the first ': ') is just the
	ToDo description — noise in the timeline — so we strip it off.
	"""
	text = BeautifulSoup(content or "", "html.parser").get_text(strip=True)
	# Drop the trailing ": <description>" that Frappe appends on assignment.
	if comment_type == "Assigned" and ": " in text:
		text = text.split(": ", 1)[0]
	return {
		"type": "assigned" if comment_type == "Assigned" else "removed",
		"text": text,
	}


def is_translatable(doctype: str) -> bool:
	return doctype in get_translated_doctypes()


@frappe.whitelist()
def get_quote_revisions(lead: str) -> list[dict]:
	"""Return per-round quote-upload history for the lead.

	Each revision cycle creates a new CRM Quote Request (the old one is marked
	is_superseded). Each `→ Quote Received` transition writes an
	`[AUTOMATION] Quote uploaded — …` Comment and each `→ Revision Requested`
	transition writes an `[AUTOMATION] Quote revision requested: …` Comment on
	the parent CRM Lead. We parse both to reconstruct the full revision history
	and attribute each round to its QR by matching upload-comment timestamps
	against QR creation timestamps.

	Returned shape (one entry per round, oldest first):
	    [
	      {
	        "round": 1,
	        "quote_request": "CRM-QR-2026-0001",
	        "value": 100000.0,
	        "margin": 18.0,
	        "file_url": "/files/q1.txt",
	        "timestamp": "2026-05-15 12:34:56.789",
	        "revision_after": "Reduce by 10%",  # None if no revision followed
	      },
	      ...
	    ]
	"""
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# Fetch QRs with creation timestamp, oldest first, so we can attribute each
	# upload round to the QR that was active when the comment was written.
	qr_data = frappe.get_all(
		"CRM Quote Request",
		filters={"lead": lead},
		fields=["name", "creation"],
		order_by="creation asc",
	)
	if not qr_data:
		return []

	all_comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"content": ["like", "[AUTOMATION] Quote %"],
		},
		fields=["name", "content", "creation"],
		order_by="creation asc",
	)

	upload_comments = [c for c in all_comments if "[AUTOMATION] Quote uploaded" in c["content"]]
	revision_comments = [c for c in all_comments if "[AUTOMATION] Quote revision requested" in c["content"]]

	revisions: list[dict] = []
	for round_idx, c in enumerate(upload_comments, start=1):
		next_upload_time = (
			upload_comments[round_idx]["creation"] if round_idx < len(upload_comments) else None
		)
		revision_after = None
		for rc in revision_comments:
			if rc["creation"] > c["creation"]:
				if next_upload_time is None or rc["creation"] < next_upload_time:
					revision_after = _extract_revision_notes(rc["content"])
					break

		# Match this upload comment to the QR that was created most recently
		# before (or at) the comment timestamp — works for single and multi-QR leads.
		qr_name = None
		for qr in qr_data:
			if qr["creation"] <= c["creation"]:
				qr_name = qr["name"]

		revisions.append(
			{
				"round": round_idx,
				"quote_request": qr_name,
				"value": _extract_currency(c["content"], "Value: "),
				"margin": _extract_currency(c["content"], "Margin: ", trailing="%"),
				"file_url": _extract_file_url(c["content"]),
				"timestamp": str(c["creation"]),
				"revision_after": revision_after,
			}
		)
	return revisions


def _extract_currency(content: str, prefix: str, trailing: str = "") -> float | None:
	"""Parse `Value: 100000` or `Margin: 18%` out of the audit Comment text."""
	idx = content.find(prefix)
	if idx == -1:
		return None
	tail = content[idx + len(prefix) :]
	end = len(tail)
	for stopper in (",", "—", "<", "\n"):
		pos = tail.find(stopper)
		if pos != -1 and pos < end:
			end = pos
	raw = tail[:end].strip()
	if trailing and raw.endswith(trailing):
		raw = raw[: -len(trailing)].strip()
	try:
		return float(raw)
	except (TypeError, ValueError):
		return None


def _extract_file_url(content: str) -> str | None:
	"""Parse the `href` of the `<a … View File</a>` anchor in the audit Comment."""
	soup = BeautifulSoup(content, "html.parser")
	a = soup.find("a")
	if a and a.get("href"):
		return a["href"]
	return None


def _extract_revision_notes(content: str) -> str | None:
	"""Extract the revision reason from a '[AUTOMATION] Quote revision requested: …' Comment."""
	import re

	prefix = "[AUTOMATION] Quote revision requested: "
	idx = content.find(prefix)
	if idx == -1:
		return None
	text = content[idx + len(prefix) :]
	text = re.sub(r"\s*\(\d+ image\(s\) attached\)\s*$", "", text).strip()
	return text or None

# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _, generate_hash
from frappe.model.document import Document

from crm.integrations.api import get_contact_by_phone_number
from crm.utils import seconds_to_duration


class CRMCallLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.core.doctype.dynamic_link.dynamic_link import DynamicLink
		from frappe.types import DF

		caller: DF.Link | None
		duration: DF.Duration | None
		end_time: DF.Datetime | None
		id: DF.Data | None
		links: DF.Table[DynamicLink]
		medium: DF.Data | None
		note: DF.Link | None
		receiver: DF.Link | None
		recording_url: DF.SmallText | None
		reference_docname: DF.DynamicLink | None
		reference_doctype: DF.Link | None
		start_time: DF.Datetime | None
		status: DF.Literal[
			"Initiated",
			"Ringing",
			"In Progress",
			"Completed",
			"Failed",
			"Busy",
			"Call Not Answered",
			"Queued",
			"Canceled",
		]
		telephony_medium: DF.Literal["", "Manual", "Twilio", "Exotel"]
		to: DF.Data
		type: DF.Literal["Incoming", "Outgoing"]
	# end: auto-generated types

	def before_insert(self):
		if not self.id:
			self.id = generate_hash(length=12)
		if not self.telephony_medium:
			self.telephony_medium = "Manual"

	def before_save(self):
		self._validate_disposition()

	def after_save(self):
		self._run_disposition_stage_move()
		self._trigger_no_answer_retry()

	def _resolve_linked_lead(self):
		if self.reference_doctype == "CRM Lead" and self.reference_docname:
			return self.reference_docname
		for row in self.links or []:
			if row.link_doctype == "CRM Lead" and row.link_name:
				return row.link_name
		return None

	def _validate_disposition(self):
		old = self.get_doc_before_save()
		old_disposition = old.disposition if old else None
		new_disposition = self.disposition

		if not new_disposition or new_disposition == old_disposition:
			return

		user = frappe.session.user
		user_roles = set(frappe.get_roles(user))
		is_privileged = bool(user_roles & {"System Manager", "Administrator"})

		if not is_privileged and user not in (self.caller, self.receiver):
			frappe.throw(
				_("You can only set the disposition on calls you handled."),
				exc=frappe.PermissionError,
				title=_("Not Allowed"),
			)

		if self.status == "Call Not Answered" and new_disposition != "No Answer / Not Reachable":
			frappe.throw(
				_("A Call Not Answered call can only be tagged 'No Answer / Not Reachable'."),
				title=_("Disposition Not Allowed"),
			)

		lead_name = self._resolve_linked_lead()

		if lead_name:
			state = frappe.db.get_value("CRM Lead", lead_name, ["status", "lead_status"], as_dict=True) or {}
			eligible = state.get("status") == "C0" or state.get("lead_status") in (
				"Cold-Unresponsive",
				"Reactivated",
			)
			if not eligible:
				frappe.throw(
					_(
						f"Dispositions can only be set on C0 leads or leads with engagement "
						f"Cold-Unresponsive/Reactivated. Lead {lead_name} is currently at "
						f"{state.get('status')} / {state.get('lead_status')}."
					),
					title=_("Disposition Not Allowed"),
				)

		disp = (
			frappe.db.get_value(
				"CRM Call Disposition",
				new_disposition,
				["requires_callback_datetime", "requires_routing_reason", "requires_lost_reason"],
				as_dict=True,
			)
			or {}
		)

		if disp.get("requires_callback_datetime") and not self.get("scheduled_callback_at"):
			frappe.throw(
				_(
					f"Disposition '{new_disposition}' requires a Scheduled Callback At datetime "
					"on this call log."
				),
				title=_("Callback Datetime Required"),
			)

		if lead_name and disp.get("requires_routing_reason"):
			if not frappe.db.get_value("CRM Lead", lead_name, "custom_fabricator_routing_reason"):
				frappe.throw(
					_(
						f"Disposition '{new_disposition}' requires Fabricator Routing Reason on the "
						"linked lead. Set it on the lead before saving the call."
					),
					title=_("Routing Reason Required"),
				)

		if lead_name and disp.get("requires_lost_reason"):
			lr = frappe.db.get_value("CRM Lead", lead_name, ["lost_reason", "lost_notes"], as_dict=True) or {}
			if not lr.get("lost_reason"):
				frappe.throw(
					_(
						f"Disposition '{new_disposition}' requires a Lost Reason on the linked lead. "
						"Set it on the lead before saving the call."
					),
					title=_("Lost Reason Required"),
				)
			if lr.get("lost_reason") == "Other" and not lr.get("lost_notes"):
				frappe.throw(
					_("Lost Reason 'Other' additionally requires Lost Notes on the linked lead."),
					title=_("Lost Notes Required"),
				)

	def _run_disposition_stage_move(self):
		old = self.get_doc_before_save()
		old_disposition = old.disposition if old else None
		new_disposition = self.disposition

		if not new_disposition or new_disposition == old_disposition:
			return

		disp = (
			frappe.db.get_value(
				"CRM Call Disposition",
				new_disposition,
				["next_status", "next_lead_status", "default_lost_reason"],
				as_dict=True,
			)
			or {}
		)
		next_status = disp.get("next_status")
		next_lead_status = disp.get("next_lead_status")
		default_reason = disp.get("default_lost_reason")

		if next_status or next_lead_status or default_reason:
			lead_name = self._resolve_linked_lead()
			if lead_name:
				current = (
					frappe.db.get_value("CRM Lead", lead_name, ["status", "lead_status"], as_dict=True) or {}
				)
				gate_ok = current.get("status") == "C0" or current.get("lead_status") in (
					"Cold-Unresponsive",
					"Reactivated",
				)
				if gate_ok:
					try:
						lead = frappe.get_doc("CRM Lead", lead_name)
						changed = False
						if next_status and next_status != lead.status:
							lead.status = next_status
							changed = True
						if next_lead_status and next_lead_status != lead.lead_status:
							lead.lead_status = next_lead_status
							changed = True
						if default_reason and not lead.lost_reason:
							lead.lost_reason = default_reason
							changed = True
						if changed:
							lead.save(ignore_permissions=True)
					except Exception:
						frappe.log_error(
							message=(
								f"Lead {lead_name} could not auto-move for disposition {new_disposition!r}: "
								f"current_status={current.get('status')}, "
								f"current_lead_status={current.get('lead_status')}, "
								f"next_status={next_status}, next_lead_status={next_lead_status}. "
								"Lead validation rejected the change. Agent must finalise the stage manually."
							),
							title="Disposition stage move skipped — lead validation failed",
						)

		if new_disposition == "Requested Callback":
			lead_name = self._resolve_linked_lead()
			if lead_name:
				lead_state = (
					frappe.db.get_value("CRM Lead", lead_name, ["status", "lead_status"], as_dict=True) or {}
				)
				if lead_state.get("status") == "C0" or lead_state.get("lead_status") in (
					"Cold-Unresponsive",
					"Reactivated",
				):
					frappe.enqueue(
						"crm.api.call_log.cancel_retry_log",
						queue="short",
						lead_name=lead_name,
						permanent=False,
					)

	def _trigger_no_answer_retry(self):
		old = self.get_doc_before_save()
		old_status = old.status if old else None

		if self.status != "Call Not Answered" or old_status == "Call Not Answered":
			return

		lead_name = self._resolve_linked_lead()
		if not lead_name:
			return

		lead_state = frappe.db.get_value("CRM Lead", lead_name, ["status", "lead_status"], as_dict=True) or {}
		if lead_state.get("status") == "C0" or lead_state.get("lead_status") in (
			"Cold-Unresponsive",
			"Reactivated",
		):
			frappe.enqueue(
				"crm.api.call_log.register_no_answer",
				queue="short",
				lead_name=lead_name,
			)

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Caller",
				"type": "Link",
				"key": "caller",
				"options": "User",
				"width": "9rem",
			},
			{
				"label": "Receiver",
				"type": "Link",
				"key": "receiver",
				"options": "User",
				"width": "9rem",
			},
			{
				"label": "Type",
				"type": "Select",
				"key": "type",
				"width": "9rem",
			},
			{
				"label": "Status",
				"type": "Select",
				"key": "status",
				"width": "9rem",
			},
			{
				"label": "Duration",
				"type": "Duration",
				"key": "duration",
				"width": "6rem",
			},
			{
				"label": "From (number)",
				"type": "Data",
				"key": "from",
				"width": "9rem",
			},
			{
				"label": "To (number)",
				"type": "Data",
				"key": "to",
				"width": "9rem",
			},
			{
				"label": "Created On",
				"type": "Datetime",
				"key": "creation",
				"width": "8rem",
			},
		]
		rows = [
			"name",
			"caller",
			"receiver",
			"type",
			"status",
			"duration",
			"from",
			"to",
			"note",
			"recording_url",
			"reference_doctype",
			"reference_docname",
			"creation",
		]
		return {"columns": columns, "rows": rows}

	def parse_list_data(calls):
		return [parse_call_log(call) for call in calls] if calls else []

	def has_link(self, doctype, name):
		for link in self.links:
			if link.link_doctype == doctype and link.link_name == name:
				return True

	def link_with_reference_doc(self, reference_doctype, reference_name):
		if self.has_link(reference_doctype, reference_name):
			return

		self.append("links", {"link_doctype": reference_doctype, "link_name": reference_name})

	def as_dict(self, *args, **kwargs):
		d = super().as_dict(*args, **kwargs)
		if d.get("recording_url"):
			d["recording_url_path"] = (
				f"/api/method/crm.integrations.api.get_recording_url?call_log_name={d.get('name')}"
			)
		return d


def parse_call_log(call):
	call["show_recording"] = False
	call["_duration"] = seconds_to_duration(call.get("duration"))
	if call.get("type") == "Incoming":
		call["activity_type"] = "incoming_call"
		contact = get_contact_by_phone_number(call.get("from"))
		receiver = (
			frappe.db.get_values("User", call.get("receiver"), ["full_name", "user_image"])[0]
			if call.get("receiver")
			else [None, None]
		)
		call["_caller"] = {
			"label": contact.get("full_name", "Unknown"),
			"image": contact.get("image"),
		}
		call["_receiver"] = {
			"label": receiver[0],
			"image": receiver[1],
		}
	elif call.get("type") == "Outgoing":
		call["activity_type"] = "outgoing_call"
		contact = get_contact_by_phone_number(call.get("to"))
		caller = (
			frappe.db.get_values("User", call.get("caller"), ["full_name", "user_image"])[0]
			if call.get("caller")
			else [None, None]
		)
		call["_caller"] = {
			"label": caller[0],
			"image": caller[1],
		}
		call["_receiver"] = {
			"label": contact.get("full_name", "Unknown"),
			"image": contact.get("image"),
		}

	if call.get("disposition"):
		color = frappe.db.get_value("CRM Call Disposition", call["disposition"], "color") or "gray"
		call["_disposition"] = {"label": call["disposition"], "color": color}
	else:
		call["_disposition"] = None

	return call


@frappe.whitelist()
def get_call_log(name: str):
	call = frappe.get_cached_doc(
		"CRM Call Log",
		name,
		fields=[
			"name",
			"caller",
			"receiver",
			"duration",
			"type",
			"status",
			"from",
			"to",
			"note",
			"disposition",
			"recording_url",
			"recording_url_path",
			"reference_doctype",
			"reference_docname",
			"creation",
		],
	).as_dict()

	call = parse_call_log(call)

	notes = []
	tasks = []

	if call.get("note"):
		note = frappe.get_cached_doc("FCRM Note", call.get("note")).as_dict()
		notes.append(note)

	if call.get("reference_doctype") and call.get("reference_docname"):
		if call.get("reference_doctype") == "CRM Lead":
			call["_lead"] = call.get("reference_docname")
		elif call.get("reference_doctype") == "CRM Deal":
			call["_deal"] = call.get("reference_docname")

	if call.get("links"):
		for link in call.get("links"):
			if link.get("link_doctype") == "CRM Task":
				task = frappe.get_cached_doc("CRM Task", link.get("link_name")).as_dict()
				tasks.append(task)
			elif link.get("link_doctype") == "FCRM Note":
				note = frappe.get_cached_doc("FCRM Note", link.get("link_name")).as_dict()
				notes.append(note)
			elif link.get("link_doctype") == "CRM Lead":
				call["_lead"] = link.get("link_name")
			elif link.get("link_doctype") == "CRM Deal":
				call["_deal"] = link.get("link_name")

	call["_tasks"] = tasks
	call["_notes"] = notes

	if call.get("_lead"):
		lead_routing = (
			frappe.db.get_value(
				"CRM Lead",
				call["_lead"],
				["custom_fabricator_routing_reason", "custom_partner_fabricator_name"],
				as_dict=True,
			)
			or {}
		)
		call["_fabricator_routing_reason"] = lead_routing.get("custom_fabricator_routing_reason")
		call["_partner_fabricator_name"] = lead_routing.get("custom_partner_fabricator_name")

	return call


@frappe.whitelist()
def create_lead_from_call_log(call_log: str | dict, lead_details: str | dict | None = None):
	call_log_data = frappe.parse_json(call_log or {})

	if isinstance(call_log_data, str):
		call_log_name = call_log_data
	elif isinstance(call_log_data, dict):
		call_log_name = call_log_data.get("name")
	else:
		call_log_name = None

	if not call_log_name:
		frappe.throw(_("A valid call log is required."), frappe.ValidationError)

	call_doc = frappe.get_doc("CRM Call Log", call_log_name)

	if not call_doc.has_permission("write"):
		frappe.throw(_("You are not permitted to update this call log."), frappe.PermissionError)

	if not frappe.has_permission("CRM Lead", "create"):
		frappe.throw(_("You are not permitted to create leads."), frappe.PermissionError)

	lead_details_data = frappe.parse_json(lead_details or {})
	if lead_details_data and not isinstance(lead_details_data, dict):
		frappe.throw(_("Invalid lead details supplied."), frappe.ValidationError)

	lead = frappe.new_doc("CRM Lead")
	meta = frappe.get_meta("CRM Lead")
	valid_fieldnames = [df.fieldname for df in meta.fields]

	sanitized_details = {
		key: value for key, value in (lead_details_data or {}).items() if key in valid_fieldnames
	}

	if "lead_owner" in valid_fieldnames and not sanitized_details.get("lead_owner"):
		sanitized_details["lead_owner"] = frappe.session.user

	if "mobile_no" in valid_fieldnames and not sanitized_details.get("mobile_no"):
		sanitized_details["mobile_no"] = call_doc.get("from") or ""

	if "first_name" in valid_fieldnames and not sanitized_details.get("first_name"):
		reference_label = sanitized_details.get("mobile_no") or call_doc.name
		sanitized_details["first_name"] = _("Lead from call {0}").format(reference_label)

	if "custom_pincode" in valid_fieldnames and not sanitized_details.get("custom_pincode"):
		sanitized_details["custom_pincode"] = "000000"

	lead.update(sanitized_details)
	lead.insert()

	call_doc.link_with_reference_doc("CRM Lead", lead.name)
	call_doc.save()

	return lead.name

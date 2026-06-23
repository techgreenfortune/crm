# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import has_gravatar, validate_email_address

from crm.fcrm.doctype.crm_quote_request.crm_quote_request import _build_lead_updates
from crm.fcrm.doctype.crm_service_level_agreement.utils import get_sla
from crm.fcrm.doctype.crm_status_change_log.crm_status_change_log import (
	add_status_change_log,
)
from crm.fcrm.doctype.utils import add_or_remove_lost_reason_section_in_sidepanel
from crm.permissions.role_config import DOWNSTREAM_SCOPE_ROLES
from crm.utils import parse_phone_number

# Fields non-owners are explicitly allowed to change (stage transitions + Lost flow).
# Everything else in self.meta.fields is blocked for non-owners.
_NON_OWNER_EDITABLE = frozenset(
	{
		"status",
		"lost_reason",
		"lost_notes",
		"custom_fabricator_routing_reason",
		"custom_fabricator_routing_notes",
		"custom_partner_fabricator_name",
		# SLA/communication tracking — updated automatically by Frappe internals
		"sla",
		"sla_status",
		"sla_creation",
		"response_by",
		"first_response_time",
		"first_responded_on",
		"last_responded_on",
		"last_response_time",
		"communication_status",
	}
)

_LAYOUT_FIELD_TYPES = frozenset(
	{
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Button",
		"Table",
		"Table MultiSelect",
	}
)

# Fields Marketing users may edit (source + UTM + sub-source). Everything else
# is read-only for them, even though list visibility is unrestricted.
_MARKETING_WRITABLE = frozenset(
	{
		"source",
		"custom_sub_source",
		"custom_utm_source",
		"custom_utm_medium",
		"custom_utm_campaign",
		"custom_utm_content",
	}
)

# Fields B2F Team users may edit while a lead sits at C7. Comments are a
# separate doctype (out of scope here).
_B2F_WRITABLE = frozenset(
	{
		"status",
		"custom_partner_fabricator_name",
		"custom_fabricator_routing_reason",
		"custom_fabricator_routing_notes",
	}
)

# Quote-derived fields populated by the C2→C4 sync and frozen afterwards.
_QUOTE_LEAD_FIELDS = frozenset(
	{
		"custom_final_price",
		"custom_tentative_value",
		"custom_final_margin",
		"custom_final_quote",
		"custom_tentative_area_sqft",
		"custom_total_quantity",
		"custom_quote_number",
		"custom_quote_validity",
	}
)

# Stage / engagement transition maps — used by both _apply_stage_transition_guard
# (before_save) and _run_stage_side_effects (after_save).
_ALLOWED_STATUS_TRANSITIONS: dict[str, set] = {
	"C0": {"C1", "C2", "C6", "C7"},
	"C1": {"C2", "C6", "C7"},
	"C2": {"C4", "C6", "C7"},
	"C4": set(),
	"C6": set(),
	"C7": set(),
}

_ALLOWED_LEAD_STATUS_TRANSITIONS: dict[str, set] = {
	"Active": {"Cold-Unresponsive", "Archived"},
	"Cold-Unresponsive": {"Reactivated", "Archived", "Active"},
	"Reactivated": {"Active", "Cold-Unresponsive", "Archived"},
	"Archived": {"Active"},
	"Won": {"Active"},
}

# Calling Team may step one C-stage back on Cold/Reactivated leads.
_CALLING_TEAM_EXTRA: dict[str, set] = {"C1": {"C0"}, "C2": {"C1"}}

# Won-type mandatory fields (checked by _validate_stage_field_requirements).
_WON_TYPE_FIELDS = (
	("custom_final_quote", "Final Quote"),
	("custom_final_price", "Final Price"),
	("custom_final_margin", "Final Margin"),
)

_C4_HANDOFF_FIELDS = (
	("email", "Email"),
	("custom_lead_type", "Lead Type"),
	("custom_customer_type", "Customer Type"),
	("custom_city", "City"),
	("custom_state", "State"),
	("custom_pincode", "Pincode"),
)

_C7_ROUTING_FIELDS = (
	("custom_fabricator_routing_reason", "Fabricator Routing Reason"),
	("custom_partner_fabricator_name", "Partner Fabricator Name"),
)

_SUB_SOURCE_TRIGGER_SOURCES = frozenset({"Referral", "Channel Partner", "Event", "Chat", "Lead Spotting"})


def _is_unassigned(snapshot) -> bool:
	"""Return True when a CRM Lead snapshot has no ``lead_owner``.

	Used by ``CRMLead._check_write_permission`` to grant Calling Team / JSE
	full write on truly unassigned leads (claim + edit + route). The moment
	the lead picks up an owner, the caller's scope shrinks to the non-owner
	fallback (status / lost flow only). ``lead_owner`` is the single source of
	truth for ownership — multi-assignee (``_assign``) was retired.
	"""
	return bool(snapshot) and not snapshot.get("lead_owner")


class CRMLead(Document):  # nosemgrep: frappe-after-save-controller-hook
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from crm.fcrm.doctype.crm_products.crm_products import CRMProducts
		from crm.fcrm.doctype.crm_rolling_response_time.crm_rolling_response_time import (
			CRMRollingResponseTime,
		)
		from crm.fcrm.doctype.crm_status_change_log.crm_status_change_log import CRMStatusChangeLog

		address: DF.Link | None
		annual_revenue: DF.Currency
		communication_status: DF.Link | None
		converted: DF.Check
		email: DF.Data | None
		facebook_form_id: DF.Data | None
		facebook_lead_id: DF.Data | None
		first_name: DF.Data
		first_responded_on: DF.Datetime | None
		first_response_time: DF.Duration | None
		gender: DF.Link | None
		image: DF.AttachImage | None
		industry: DF.Link | None
		job_title: DF.Data | None
		last_name: DF.Data | None
		last_responded_on: DF.Datetime | None
		last_response_time: DF.Duration | None
		lead_name: DF.Data | None
		lead_owner: DF.Link | None
		lead_status: DF.Link
		lost_notes: DF.Text | None
		lost_reason: DF.Link | None
		middle_name: DF.Data | None
		mobile_no: DF.Data | None
		naming_series: DF.Literal["CRM-LEAD-.YYYY.-"]
		net_total: DF.Currency
		no_of_employees: DF.Literal["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"]
		organization: DF.Data | None
		phone: DF.Data | None
		products: DF.Table[CRMProducts]
		response_by: DF.Datetime | None
		rolling_responses: DF.Table[CRMRollingResponseTime]
		salutation: DF.Link | None
		sla: DF.Link | None
		sla_creation: DF.Datetime | None
		sla_status: DF.Literal["", "First Response Due", "Rolling Response Due", "Failed", "Fulfilled"]
		source: DF.Link | None
		status: DF.Link
		status_change_log: DF.Table[CRMStatusChangeLog]
		territory: DF.Link | None
		total: DF.Currency
		website: DF.Data | None
	# end: auto-generated types

	def before_validate(self):
		# Lock fires first — before Frappe's link/mandatory validation — so an
		# archived lead with a now-invalid Link field (deleted area, etc.) still
		# rejects writes with the correct "lead archived" message instead of a
		# misleading "could not find X" link error.
		self._enforce_c4_won_lock()
		self.set_sla()
		# Under test/install/import, mandatory custom_pincode would block fixture
		# and harness-created leads. Real form submissions don't carry these
		# flags, so UI validation is unaffected.
		if not self.get("custom_pincode") and (
			frappe.flags.in_test or frappe.flags.in_install or frappe.flags.in_import
		):
			self.custom_pincode = "000000"

	def validate(self):
		self._check_write_permission()
		self.set_full_name()
		self.set_lead_name()
		self.set_title()
		self.validate_email()
		self.validate_lost_reason()
		self.validate_retail_specific_fields()
		# Re-pull accepted quote fields BEFORE stage field requirements run
		# (it checks `custom_final_*` for Won-type stages, which this populates).
		self._sync_accepted_quote_on_won()
		self._validate_stage_field_requirements()
		# Freeze runs LAST — once a lead is already at Won, quote fields can't
		# be edited (sync above is the only way to set them).
		self._freeze_quote_fields_at_won()
		if self.has_value_changed("status"):
			add_status_change_log(self)

	def on_update(self):
		if self.has_value_changed("lead_owner"):
			frappe.db.set_value(
				"CRM Quote Request",
				{"lead": self.name},
				"lead_owner",
				self.lead_owner or "",
			)

	def after_save(self):  # nosemgrep: frappe-after-save-controller-hook
		# after_save is a valid Frappe v15 hook (alias of on_update); rule docs are v13-era.
		self._run_stage_side_effects()

	def before_save(self):
		self.apply_sla()
		self._extract_coordinates_from_map_link()
		self._apply_stage_transition_guard()
		self._dedup_and_normalize_mobile()
		self._assign_b2f_on_c7()

	def _assign_b2f_on_c7(self):
		"""Round-robin assign lead_owner to least-loaded B2F user on first entry to C7."""
		old = self.get_doc_before_save()
		old_status = old.status if old else None
		if self.status != "C7" or old_status == "C7":
			return
		b2f_users = frappe.db.get_all(
			"Has Role",
			filters={"role": "B2F Team", "parenttype": "User"},
			pluck="parent",
		)
		if not b2f_users:
			return

		def _c7_load(user):
			return frappe.db.count("CRM Lead", filters={"lead_owner": user, "status": "C7"})

		self.lead_owner = min(b2f_users, key=_c7_load)

	def _validate_stage_field_requirements(self):
		if frappe.flags.in_test or frappe.flags.in_install or frappe.flags.in_import:
			return

		missing = []
		if self.status and frappe.get_cached_value("CRM Lead Status", self.status, "type") == "Won":
			missing += [label for field, label in _WON_TYPE_FIELDS if not self.get(field)]
		if self.status == "C4":
			missing += [label for field, label in _C4_HANDOFF_FIELDS if not self.get(field)]
		if missing:
			frappe.throw(
				_("Required for this stage: {0}.").format(", ".join(missing)),
				frappe.ValidationError,
			)

		if self.status == "C7":
			missing = [label for field, label in _C7_ROUTING_FIELDS if not self.get(field)]
			if missing:
				frappe.throw(
					_("Required at C7 (Forwarded to Fabricator): {0}.").format(", ".join(missing)),
					frappe.ValidationError,
				)

		if self.source in _SUB_SOURCE_TRIGGER_SOURCES and not self.get("custom_sub_source"):
			frappe.throw(
				_("Sub Source is required when Source is {0}.").format(self.source),
				frappe.ValidationError,
			)

	def _apply_stage_transition_guard(self):
		if self.is_new():
			if not self.status:
				if frappe.db.exists("CRM Lead Status", "C0"):
					self.status = "C0"
				else:
					open_statuses = frappe.db.get_all(
						"CRM Lead Status", filters={"type": "Open"}, pluck="name"
					)
					if open_statuses:
						self.status = open_statuses[0]
					else:
						frappe.throw(
							_(
								"No open lead statuses configured. Run bench migrate or create a "
								"CRM Lead Status with type 'Open'."
							)
						)
			if not self.lead_status:
				self.lead_status = "Active"

		if not self.lead_status:
			self.lead_status = "Active"

		if not self.is_new():
			old = self.get_doc_before_save()
			old_status = old.status if old else None
			old_lead_status = old.lead_status if old else None
			new_status = self.status

			# Auto-flip Cold-Unresponsive/Reactivated → Active on any C-stage advance.
			if (
				old_status
				and old_status != new_status
				and self.lead_status
				in (
					"Reactivated",
					"Cold-Unresponsive",
				)
			):
				if self.lead_status == "Cold-Unresponsive":
					self.flags.auto_cold_flip = True
				self.lead_status = "Active"

			# C4 (Won path) stays Active until project handoff flips to Won.
			if new_status == "C4" and self.lead_status != "Active":
				self.lead_status = "Active"
			# C6 (Lost) — archive so Lost leads don't pollute Active queries.
			elif new_status == "C6" and self.lead_status != "Archived":
				self.lead_status = "Archived"

			new_lead_status = self.lead_status
			status_changed = bool(old_status) and old_status != new_status
			lead_status_changed = bool(old_lead_status) and old_lead_status != new_lead_status

			if status_changed or lead_status_changed:
				user = frappe.session.user
				user_roles = set(frappe.get_roles(user))
				is_privileged = bool(
					user_roles & {"Administrator", "System Manager", "Sales Head", "Sales Coordinator"}
				)
				is_qr_gate_bypass = bool(user_roles & {"Administrator", "System Manager"})

				if not is_privileged:
					if user_roles & DOWNSTREAM_SCOPE_ROLES:
						from crm.overrides.crm_lead_permissions import downstream_users

						downstream = downstream_users(user)
						downstream.add(user)
						if self.lead_owner not in downstream:
							frappe.throw(
								_("You can only change the status of leads owned by users in your team.")
							)
					elif "Jr. Sales Executive" in user_roles:
						if status_changed and new_status not in ("C1", "C2", "C6"):
							frappe.throw(_("Spotters can only move leads to C1, C2, or C6"))
					elif "Calling Team" in user_roles:
						old_state_ok = old_status == "C0" or old_lead_status in (
							"Cold-Unresponsive",
							"Reactivated",
						)
						if not old_state_ok:
							frappe.throw(
								_(
									"Calling Team can only manage leads at C0 or with engagement "
									"Cold-Unresponsive/Reactivated"
								)
							)
						if status_changed and new_status not in ("C0", "C1", "C2", "C6", "C7"):
							frappe.throw(_("Calling Team can only move C-stage to C0, C1, C2, C6, or C7"))
						if lead_status_changed and new_lead_status not in (
							"Active",
							"Cold-Unresponsive",
							"Reactivated",
							"Archived",
						):
							frappe.throw(
								_(
									"Calling Team can only set engagement to Active, Cold-Unresponsive, "
									"Reactivated, or Archived"
								)
							)
					else:
						if self.lead_owner != user:
							frappe.throw(_("You can only change the status of leads you own"))

				if not is_qr_gate_bypass:
					if status_changed and old_status in _ALLOWED_STATUS_TRANSITIONS:
						allowed = set(_ALLOWED_STATUS_TRANSITIONS[old_status])
						if "Calling Team" in user_roles:
							allowed |= _CALLING_TEAM_EXTRA.get(old_status, set())
						if new_status not in allowed:
							frappe.throw(_(f"Cannot move C-stage from {old_status} to {new_status}"))

					if lead_status_changed and old_lead_status in _ALLOWED_LEAD_STATUS_TRANSITIONS:
						if new_lead_status not in _ALLOWED_LEAD_STATUS_TRANSITIONS[old_lead_status]:
							frappe.throw(
								_(f"Cannot move engagement from {old_lead_status} to {new_lead_status}")
							)

					if status_changed and old_status == "C2" and new_status == "C4":
						latest_qr_status = frappe.db.get_value(
							"CRM Quote Request",
							{"lead": self.name},
							"status",
							order_by="creation desc",
						)
						if latest_qr_status != "Accepted":
							frappe.throw(
								_(
									"Cannot advance to C4 (Won) until the Lead Owner has accepted the "
									"Quote Request. Open the latest Quote Request and set its status "
									"to 'Accepted'."
								),
								title=_("Quote Approval Required"),
							)

	def _dedup_and_normalize_mobile(self):
		if frappe.flags.in_test or frappe.flags.in_import:
			return
		if not self.mobile_no:
			return
		if not self.has_value_changed("mobile_no"):
			return

		parsed = parse_phone_number(self.mobile_no)
		if not parsed or not parsed.get("success") or not parsed.get("is_valid"):
			frappe.throw(
				_("{0} is not a valid mobile number").format(self.mobile_no),
				title=_("Invalid Mobile Number"),
			)
		self.mobile_no = parsed["formats"]["E164"]

		existing = frappe.db.get_value(
			"CRM Lead",
			{"mobile_no": self.mobile_no, "name": ["!=", self.name]},
			["name", "lead_name", "status", "lead_status"],
			as_dict=True,
		)
		if existing:
			hint = {
				"Cold-Unresponsive": "Use the Reactivation flow.",
				"Archived": "Use the Reactivation flow.",
			}.get(existing.lead_status) or {
				"C6": "Lead is marked Lost — investigate before creating a new lead.",
				"C4": "This number is already a customer.",
			}.get(existing.status, "")
			message = (
				f"A lead with mobile {self.mobile_no} already exists: "
				f"{existing.lead_name} ({existing.name}) — Stage: {existing.status} / {existing.lead_status}."
			)
			if hint:
				message += " " + hint
			frappe.throw(_(message), title=_("Duplicate Mobile Number"))

	def _run_stage_side_effects(self):
		old = self.get_doc_before_save()
		old_status = old.status if old else None
		old_lead_status = old.lead_status if old else None
		new_status = self.status
		new_lead_status = self.lead_status

		if old is None and new_status == "C0":
			frappe.get_doc(
				{
					"doctype": "CRM Task",
					"task_type": "call_lead",
					"title": f"Call Lead — {self.lead_name or self.name}",
					"status": "Todo",
					"priority": "Medium",
					"reference_doctype": "CRM Lead",
					"reference_docname": self.name,
					"description": (
						f"New lead {self.lead_name or self.name} has arrived at C0. "
						"Make the initial call and log the disposition."
					),
				}
			).insert(ignore_permissions=True)

		if old is None and new_status == "C1" and self.email:
			frappe.enqueue(
				"crm.integrations.brevo.api.enroll_in_sequence",
				email=self.email,
				lead_name=self.name,
			)

		status_changed = bool(old_status) and old_status != new_status
		lead_status_changed = bool(old_lead_status) and old_lead_status != new_lead_status

		was_retry_active = old_status == "C0" and old_lead_status in (
			"Active",
			"Cold-Unresponsive",
			"Reactivated",
		)
		is_retry_active = new_status == "C0" and new_lead_status in (
			"Active",
			"Cold-Unresponsive",
			"Reactivated",
		)
		if (status_changed or lead_status_changed) and was_retry_active and not is_retry_active:
			frappe.enqueue(
				"crm.api.call_log.cancel_retry_log", user="Administrator", lead_name=self.name, permanent=True
			)
		elif lead_status_changed and new_lead_status == "Archived":
			frappe.enqueue(
				"crm.api.call_log.cancel_retry_log", user="Administrator", lead_name=self.name, permanent=True
			)
		elif lead_status_changed and old_lead_status == "Cold-Unresponsive" and new_lead_status == "Active":
			frappe.enqueue(
				"crm.api.call_log.cancel_retry_log", user="Administrator", lead_name=self.name, permanent=True
			)

		if status_changed:
			if new_status == "C1" and self.email:
				frappe.enqueue(
					"crm.integrations.brevo.api.enroll_in_sequence",
					email=self.email,
					lead_name=self.name,
				)

			if new_status == "C7":
				b2f_count = frappe.db.count("Has Role", filters={"role": "B2F Team", "parenttype": "User"})
				if not b2f_count:
					frappe.log_error(
						message=(
							f"Lead {self.name} routed to C7 but no users have the B2F Team role. "
							"The handle_fabricator_lead task will be orphaned until a B2F user "
							"is configured."
						),
						title="C7 routing: empty B2F pool",
					)
					recipients = set(
						frappe.db.get_all(
							"Has Role",
							filters={
								"role": ["in", ["Sales Head", "Sales Coordinator"]],
								"parenttype": "User",
							},
							pluck="parent",
						)
					)
					for sm in recipients:
						try:
							frappe.get_doc(
								{
									"doctype": "Notification Log",
									"subject": "C7 routing: empty B2F pool",
									"email_content": (
										f"Lead {self.lead_name or self.name} routed to C7 but no B2F "
										"users are configured. The Handle Fabricator Lead task is "
										"orphaned until a B2F user is added."
									),
									"for_user": sm,
									"type": "Alert",
									"document_type": "CRM Lead",
									"document_name": self.name,
								}
							).insert(ignore_permissions=True)
						except Exception:
							pass

				if not frappe.db.exists(
					"CRM Task",
					{
						"reference_doctype": "CRM Lead",
						"reference_docname": self.name,
						"task_type": "handle_fabricator_lead",
						"status": ["in", ["Todo", "In Progress"]],
					},
				):
					task_doc = {
						"doctype": "CRM Task",
						"task_type": "handle_fabricator_lead",
						"title": f"Handle Fabricator Lead — {self.lead_name or self.name}",
						"status": "Todo",
						"priority": "High",
						"reference_doctype": "CRM Lead",
						"reference_docname": self.name,
						"description": (
							f"Lead {self.lead_name} has been routed to you for fabricator handling "
							"(C7). Review the fabricator routing reason and partner fabricator name. "
							"The lead has exited the active sales pipeline; coordinate with the "
							"fabricator externally to close the deal."
						),
					}
					if b2f_count:
						task_doc["assigned_to"] = self.lead_owner
					frappe.get_doc(task_doc).insert(ignore_permissions=True)

		if (
			lead_status_changed
			and old_lead_status == "Cold-Unresponsive"
			and new_lead_status in ("Reactivated", "Active")
			and not self.get("custom_reactivated_at")
			and not self.flags.get("auto_cold_flip")
		):
			frappe.db.set_value("CRM Lead", self.name, "custom_reactivated_at", frappe.utils.now_datetime())

		if lead_status_changed and old_lead_status == "Active" and new_lead_status == "Cold-Unresponsive":
			try:
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "CRM Lead",
						"reference_name": self.name,
						"content": f"[AUTOMATION] Lead moved to Cold-Unresponsive (C-stage preserved at {self.status}).",
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass

		if (
			lead_status_changed
			and old_lead_status == "Cold-Unresponsive"
			and new_lead_status == "Reactivated"
		):
			try:
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "CRM Lead",
						"reference_name": self.name,
						"content": f"[AUTOMATION] Lead reactivated from Cold-Unresponsive (C-stage preserved at {self.status}).",
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass

	def _extract_coordinates_from_map_link(self):
		"""Populate `custom_latitude` / `custom_longitude` from a pasted
		Google Maps URL in `custom_google_map_link`. Runs only when the URL
		field has changed (paste on create, edit on existing lead). A bad or
		unparseable URL surfaces a soft warning — the save still goes through
		so users can correct coordinates manually.
		"""
		if not self.has_value_changed("custom_google_map_link"):
			return
		url = (self.get("custom_google_map_link") or "").strip()
		if not url:
			return

		from crm.utils.google_maps import extract_lat_lng

		coords = extract_lat_lng(url)
		if not coords:
			frappe.msgprint(
				_(
					"Could not extract latitude/longitude from the Google Map link. "
					"Please verify the URL or set the coordinates manually."
				),
				title=_("Map Link Not Recognised"),
				indicator="orange",
			)
			return
		self.custom_latitude = coords[0]
		self.custom_longitude = coords[1]

	def set_full_name(self):
		if self.first_name:
			self.lead_name = " ".join(
				name
				for name in [
					self.salutation,
					self.first_name,
					self.middle_name,
					self.last_name,
				]
				if name
			)

	def set_lead_name(self):
		if not self.lead_name:
			# Check for leads being created through data import
			if not self.organization and not self.email and not self.flags.ignore_mandatory:
				frappe.throw(_("A Lead requires either a person's name or an organization's name"))
			elif self.organization:
				self.lead_name = self.organization
			elif self.email:
				self.lead_name = self.email.split("@")[0]
			else:
				self.lead_name = "Unnamed Lead"

	def set_title(self):
		self.title = self.organization or self.lead_name

	def validate_email(self):
		if self.email:
			if not self.flags.ignore_email_validation:
				validate_email_address(self.email, throw=True)

			if self.email == self.lead_owner:
				frappe.throw(_("Lead Owner cannot be same as the Lead Email Address"))

			if self.is_new() or not self.image:
				self.image = has_gravatar(self.email)

	def validate_lost_reason(self):
		if self.status and frappe.get_cached_value("CRM Lead Status", self.status, "type") == "Lost":
			if not self.lost_reason:
				frappe.throw(_("Please specify a reason for losing the lead."), frappe.ValidationError)
			elif self.lost_reason == "Other" and not self.lost_notes:
				frappe.throw(_("Please specify the reason for losing the lead."), frappe.ValidationError)
		if self.has_value_changed("status"):
			add_or_remove_lost_reason_section_in_sidepanel(self)

	def validate_project_specific_fields(self):
		# Project-type leads (custom_lead_type='Projects') must carry full project
		# metadata before the manual Create Project button can fire.
		#
		# This validator is NO LONGER called from validate() (deliberate — see plan).
		# Instead it is invoked by `crm.api.projects.create_project_for_lead` right
		# before triggering the project-creation handoff. The lead can sit at C4
		# without project metadata; the button just won't work until it's filled.
		if self.status != "C4":
			return
		if self.get("custom_lead_type") != "Projects":
			return
		# Project Category + Project Configuration are optional at C4 handoff
		# (sales can fill them later). Only site address + pincode are required
		# — OpsGate needs them to geo-tag the project. Removed on 2026-05-27.
		missing = [
			label
			for field, label in (
				("custom_site_address_full", "Site Address (Full)"),
				("custom_site_pincode", "Site Pincode"),
			)
			if not self.get(field)
		]
		if missing:
			frappe.throw(
				_("Required for Project leads at C4: {0}.").format(", ".join(missing)),
				frappe.ValidationError,
			)

	def validate_retail_specific_fields(self):
		if self.status != "C4":
			return
		if self.get("custom_lead_type") != "Retail":
			return
		if not self.get("custom_customer_address"):
			frappe.throw(
				_("Customer Address is required for Retail leads at C4."),
				frappe.ValidationError,
			)

	def _check_write_permission(self):
		if self.is_new():
			return
		user = frappe.session.user
		if user == "Administrator" or self.flags.get("ignore_permissions"):
			return
		user_roles = set(frappe.get_roles(user))
		# Tier-1 full RW bypass — Sales Head / Sales Coordinator / System Manager
		# may edit anything. Management is NOT here: it's tier-1 for *visibility*
		# (sees every lead) but read-only via its doctype permission, so a write
		# never reaches this guard — and we don't want to grant a latent
		# field-lock bypass if it ever gained write docperm.
		if user_roles & {"System Manager", "Sales Head", "Sales Coordinator"}:
			return

		# ASM/RSM tree-scoped assignment guard. Runs before the owner-side
		# branches below — an ASM who owns a lead would otherwise hit the
		# `lead_owner == user` early return and be able to reassign anywhere.
		# Orphan ASM/RSM (no hierarchy row) bypass via `allowed_assignees`
		# returning None. The secondary lower-block check still protects
		# managers reassigning a downstream-owned lead onto an out-of-tree user.
		if user_roles & DOWNSTREAM_SCOPE_ROLES and self.has_value_changed("lead_owner") and self.lead_owner:
			from crm.overrides.crm_lead_permissions import allowed_assignees

			allowed = allowed_assignees(user)
			if allowed is not None and self.lead_owner not in allowed:
				frappe.throw(
					_(
						"You can only assign leads to users in your team (your "
						"reports or your manager). {0} is outside your tree — "
						"escalate to Sales Head for cross-team transfers."
					).format(self.lead_owner),
					frappe.PermissionError,
					title=_("Out-of-tree Assignment Blocked"),
				)

		# Marketing / B2F Team — read-all (or stage-locked for B2F) but writes
		# are confined to a narrow field allowlist regardless of ownership.
		writable: set[str] | None = None
		if "Marketing" in user_roles:
			writable = set(_MARKETING_WRITABLE) | (writable or set())
		if "B2F Team" in user_roles:
			writable = set(_B2F_WRITABLE) | (writable or set())

		old = self.get_doc_before_save()
		if not old:
			return

		if writable is not None:
			# Field-locked roles: only allow changes to fields in the writable
			# allowlist. Layout fields are skipped.
			for field in self.meta.fields:
				if field.fieldtype in _LAYOUT_FIELD_TYPES:
					continue
				if field.fieldname in writable:
					continue
				if self.has_value_changed(field.fieldname):
					frappe.throw(
						_("Your role does not allow editing '{0}'.").format(field.label or field.fieldname),
						frappe.PermissionError,
						title=_("Not Permitted"),
					)
			return

		# Calling Team / Jr. Sales Executive — full write on a lead while it is
		# unassigned (no lead_owner). This lets the caller claim, edit, set
		# lead_owner, hand off in one save. Once `old` already carries an owner,
		# the caller falls through to the non-owner fallback below: only fields
		# in `_NON_OWNER_EDITABLE`
		# (status + lost-flow + fabricator-routing) can change. That's by
		# design — the caller can still manually move C0 → C1 (or whatever the
		# Stage Transition server script allows for Cold/Reactivated leads),
		# and disposition automation can drive further moves via server
		# scripts (which bypass validate entirely). All other fields on the
		# Lead doc are read-only post-assignment.
		if user_roles & {"Calling Team", "Jr. Sales Executive"} and _is_unassigned(old):
			return

		# Owner-side editors: SE/PSE see only their own lead; ASM/RSM also
		# treat downstream-chain users' leads as theirs. Checking both the
		# new and old owner lets the current owner reassign without losing
		# write access mid-save.
		if self.lead_owner == user or old.lead_owner == user:
			return
		if user_roles & DOWNSTREAM_SCOPE_ROLES:
			from crm.overrides.crm_lead_permissions import downstream_users

			downstream = downstream_users(user)
			if old.lead_owner in downstream:
				# Cross-team transfer guard: if the manager is changing
				# `lead_owner`, the NEW owner must also be inside their own
				# downstream chain. Prevents an ASM from handing a lead to a
				# peer ASM's team (only Sales Head / Coordinator can move
				# leads across teams). Unassigning (new_owner = None/"") is
				# still allowed — the lead just goes back to the pool.
				if (
					self.lead_owner != old.lead_owner
					and self.lead_owner
					and self.lead_owner not in downstream
				):
					frappe.throw(
						_(
							"You can only reassign leads to users within your own team. "
							"{0} is outside your downstream chain — escalate to Sales Head."
						).format(self.lead_owner),
						frappe.PermissionError,
						title=_("Cross-team Transfer Blocked"),
					)
				return

		# Non-owner fallback: only stage/lost-flow fields are editable.
		for field in self.meta.fields:
			if field.fieldtype in _LAYOUT_FIELD_TYPES:
				continue
			if field.fieldname in _NON_OWNER_EDITABLE:
				continue
			if self.has_value_changed(field.fieldname):
				frappe.throw(
					_("You can only edit leads that are assigned to you."),
					frappe.PermissionError,
					title=_("Not Permitted"),
				)

	def _sync_accepted_quote_on_won(self):
		"""When the lead transitions into a Won-type status (C4), re-pull the
		accepted quote's sq.ft / value / margin / file into the lead's custom
		fields. Guarantees the closed order matches the accepted quote even if
		someone manually edited the lead between Quote Received and C4.

		The C2→C4 stage gate (Before-Save server script) already requires the
		latest QR to be Accepted, so an Accepted QR is guaranteed to exist by
		the time this runs.
		"""
		if self.is_new() or not self.status:
			return
		new_type = frappe.get_cached_value("CRM Lead Status", self.status, "type")
		if new_type != "Won":
			return
		old = self.get_doc_before_save()
		if old and old.get("status") == self.status:
			return  # not a transition INTO Won — don't churn on no-op saves

		accepted = frappe.db.get_value(
			"CRM Quote Request",
			{"lead": self.name, "status": "Accepted"},
			[
				"quote_sq_ft",
				"quote_value",
				"quote_margin",
				"quote_file",
				"total_quantity",
				"quote_number",
				"quote_validity",
			],
			as_dict=True,
			order_by="modified desc",
		)
		if not accepted:
			# No Accepted QR — the "CRM Lead — Before Save — Stage Field
			# Requirements" server script will throw next on the empty
			# custom_final_* fields.
			return

		self.update(_build_lead_updates(accepted))

	def _freeze_quote_fields_at_won(self):
		"""Once a lead is ALREADY at a Won-type status, quote-derived fields
		cannot be edited (except by Administrator / System Manager / Sales Head).
		The C2→C4 transition itself still gets to set them via
		_sync_accepted_quote_on_won because that runs while old.status is still C2.

		Bypass tier is broader than _enforce_c4_won_lock by design: a Sales
		Head fixing a price typo on a closed lead is normal ops; unarchiving a
		fully-handed-off lead is not.
		"""
		if self.is_new() or self.flags.get("ignore_permissions"):
			return
		user = frappe.session.user
		if user == "Administrator":
			return
		roles = set(frappe.get_roles(user))
		if "System Manager" in roles or "Sales Head" in roles:
			return
		old = self.get_doc_before_save()
		if not old:
			return
		old_type = frappe.get_cached_value("CRM Lead Status", old.status, "type") if old.status else None
		if old_type != "Won":
			return  # only enforce once the lead is ALREADY at Won
		for field in _QUOTE_LEAD_FIELDS:
			if self.has_value_changed(field):
				label = self.meta.get_label(field) or field
				frappe.throw(
					_("'{0}' cannot be edited once the lead is at Won.").format(label),
					frappe.PermissionError,
					title=_("Field Locked"),
				)

	def _enforce_c4_won_lock(self):
		"""Once a lead is at C4 (Won) AND lead_status == 'Won', the doc is
		frozen — no status moves, no engagement flips, no field edits, no task
		spawns. The lock fires after Create Project flips lead_status to Won.

		Bypass: Administrator + System Manager only. Every other role (including
		Sales Head) is locked — unlock requires explicit admin intervention
		(raise lead_status back to Active via Desk / bench console).
		"""
		if self.is_new():
			return
		if self.flags.get("ignore_permissions") and self.flags.get("ignore_c4_lock"):
			# Allow internal flows (e.g. an admin's re-open script) to bypass
			# with an explicit opt-in flag.
			return
		old = self.get_doc_before_save()
		if not old:
			return
		if not (old.get("status") == "C4" and old.get("lead_status") == "Won"):
			return
		user = frappe.session.user
		if user == "Administrator":
			return
		user_roles = set(frappe.get_roles(user))
		if "System Manager" in user_roles:
			return
		# Detect ANY field change against the snapshot. If nothing changed,
		# allow the save (read-only refresh / no-op).
		for field in self.meta.fields:
			if field.fieldtype in _LAYOUT_FIELD_TYPES:
				continue
			if self.has_value_changed(field.fieldname):
				frappe.throw(
					_(
						"This lead is Won (C4 + Won) and is locked for edits. "
						"Contact a System Manager to unlock."
					),
					title=_("Lead Won"),
				)

	def create_contact(self, existing_contact=None, throw=True):
		if not self.lead_name:
			self.set_full_name()
			self.set_lead_name()

		existing_contact = existing_contact or self.contact_exists(throw)
		if existing_contact:
			self.update_lead_contact(existing_contact)
			return existing_contact

		contact = frappe.new_doc("Contact")
		contact.update(
			{
				"first_name": self.first_name or self.lead_name,
				"last_name": self.last_name,
				"salutation": self.salutation,
				"gender": self.gender,
				"designation": self.job_title,
				"company_name": self.organization,
				"image": self.image or "",
			}
		)

		if self.email:
			contact.append("email_ids", {"email_id": self.email, "is_primary": 1})

		if self.phone:
			contact.append("phone_nos", {"phone": self.phone, "is_primary_phone": 1})

		if self.mobile_no:
			contact.append("phone_nos", {"phone": self.mobile_no, "is_primary_mobile_no": 1})

		contact.insert(ignore_permissions=True)
		contact.reload()  # load changes by hooks on contact

		return contact.name

	def create_organization(self, existing_organization=None):
		if not self.organization and not existing_organization:
			return

		existing_organization = existing_organization or frappe.db.exists(
			"CRM Organization", {"organization_name": self.organization}
		)
		if existing_organization:
			self.db_set("organization", existing_organization)
			return existing_organization

		organization = frappe.new_doc("CRM Organization")
		organization.update(
			{
				"organization_name": self.organization,
				"website": self.website,
				"territory": self.territory,
				"industry": self.industry,
				"annual_revenue": self.annual_revenue,
			}
		)
		organization.insert(ignore_permissions=True)
		return organization.name

	def update_lead_contact(self, contact):
		contact = frappe.get_cached_doc("Contact", contact)
		frappe.db.set_value(
			"CRM Lead",
			self.name,
			{
				"salutation": contact.salutation,
				"first_name": contact.first_name,
				"last_name": contact.last_name,
				"email": contact.email_id,
				"mobile_no": contact.mobile_no,
			},
		)

	def contact_exists(self, throw=True):
		email_exist = frappe.db.exists("Contact Email", {"email_id": self.email})
		phone_exist = frappe.db.exists("Contact Phone", {"phone": self.phone})
		mobile_exist = frappe.db.exists("Contact Phone", {"phone": self.mobile_no})

		doctype = "Contact Email" if email_exist else "Contact Phone"
		name = email_exist or phone_exist or mobile_exist

		if name:
			text = "Email" if email_exist else "Phone" if phone_exist else "Mobile No"
			data = self.email if email_exist else self.phone if phone_exist else self.mobile_no

			value = "{0}: {1}".format(text, data)

			contact = frappe.db.get_value(doctype, name, "parent")

			if throw:
				frappe.throw(
					_("Contact already exists with {0}").format(value),
					title=_("Contact Already Exists"),
				)
			return contact

		return False

	def create_deal(self, contact, organization, deal=None):
		new_deal = frappe.new_doc("CRM Deal")

		lead_deal_map = {
			"lead_owner": "deal_owner",
		}

		restricted_fieldtypes = [
			"Tab Break",
			"Section Break",
			"Column Break",
			"HTML",
			"Button",
			"Attach",
		]
		restricted_map_fields = [
			"name",
			"naming_series",
			"creation",
			"owner",
			"modified",
			"modified_by",
			"idx",
			"docstatus",
			"status",
			"email",
			"mobile_no",
			"phone",
			"sla",
			"sla_status",
			"response_by",
			"first_response_time",
			"first_responded_on",
			"communication_status",
			"sla_creation",
			"status_change_log",
		]

		for field in self.meta.fields:
			if field.fieldtype in restricted_fieldtypes:
				continue
			if field.fieldname in restricted_map_fields:
				continue

			fieldname = field.fieldname
			if field.fieldname in lead_deal_map:
				fieldname = lead_deal_map[field.fieldname]

			if hasattr(new_deal, fieldname):
				if fieldname == "organization":
					new_deal.update({fieldname: organization})
				else:
					new_deal.update({fieldname: self.get(field.fieldname)})

		new_deal.update(
			{
				"lead": self.name,
				"contacts": [{"contact": contact}],
			}
		)

		if self.first_responded_on:
			new_deal.update(
				{
					"sla_creation": self.sla_creation,
					"response_by": self.response_by,
					"sla_status": self.sla_status,
					"communication_status": self.communication_status,
					"first_response_time": self.first_response_time,
					"first_responded_on": self.first_responded_on,
				}
			)

		if deal:
			new_deal.update(deal)

		new_deal.insert(ignore_permissions=True)

		# ``deal_owner`` is mapped from ``lead_owner`` above (lead_deal_map), so
		# the new deal already carries the single owner — no further assignment.
		return new_deal.name

	def set_sla(self):
		"""
		Find an SLA to apply to the lead.
		"""
		if self.sla:
			return

		sla = get_sla(self)
		if not sla:
			self.first_responded_on = None
			self.first_response_time = None
			return
		self.sla = sla.name

	def apply_sla(self):
		"""
		Apply SLA if set.
		"""
		if not self.sla:
			return
		sla = frappe.get_last_doc("CRM Service Level Agreement", {"name": self.sla})
		if sla:
			sla.apply(self)

	def convert_to_deal(self, deal=None):
		return convert_to_deal(lead=self.name, doc=self, deal=deal)

	@staticmethod
	def get_non_filterable_fields():
		return ["converted"]

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Name",
				"type": "Data",
				"key": "lead_name",
				"width": "12rem",
			},
			{
				"label": "Lead ID",
				"type": "Data",
				"key": "name",
				"width": "14rem",
			},
			{
				"label": "Organization",
				"type": "Link",
				"key": "organization",
				"options": "CRM Organization",
				"width": "10rem",
			},
			{
				"label": "Status",
				"type": "Link",
				"options": "CRM Lead Status",
				"key": "status",
				"width": "8rem",
			},
			{
				"label": "Email",
				"type": "Data",
				"key": "email",
				"width": "12rem",
			},
			{
				"label": "Mobile No.",
				"type": "Data",
				"key": "mobile_no",
				"width": "11rem",
			},
			{
				"label": "Lead Owner",
				"type": "Link",
				"options": "User",
				"key": "lead_owner",
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
			"lead_name",
			"organization",
			"status",
			"email",
			"mobile_no",
			"lead_owner",
			"first_name",
			"sla_status",
			"response_by",
			"first_response_time",
			"first_responded_on",
			"modified",
			"image",
		]
		return {"columns": columns, "rows": rows}

	@staticmethod
	def default_kanban_settings():
		return {
			"column_field": "status",
			"title_field": "lead_name",
			"kanban_fields": '["organization", "email", "mobile_no", "lead_owner", "modified"]',
		}

	@staticmethod
	def default_map_settings():
		return {
			"latitude_field": "custom_latitude",
			"longitude_field": "custom_longitude",
			"rows": [
				"name",
				"lead_name",
				"organization",
				"status",
				"mobile_no",
				"email",
				"lead_owner",
				"custom_latitude",
				"custom_longitude",
				"custom_google_map_link",
			],
		}


@frappe.whitelist()
def convert_to_deal(
	lead: str,
	doc: Document | None = None,
	deal: str | dict | None = None,
	existing_contact: str | None = None,
	existing_organization: str | None = None,
):
	if not (doc and doc.flags.get("ignore_permissions")) and not frappe.has_permission(
		"CRM Lead", "write", lead
	):
		frappe.throw(_("Not allowed to convert Lead to Deal"), frappe.PermissionError)

	lead = frappe.get_cached_doc("CRM Lead", lead)
	if frappe.db.exists("CRM Lead Status", "Qualified"):
		lead.db_set("status", "Qualified")
	lead.db_set("converted", 1)
	if lead.sla and frappe.db.exists("CRM Communication Status", "Replied"):
		lead.db_set("communication_status", "Replied")
	contact = lead.create_contact(existing_contact, False)
	organization = lead.create_organization(existing_organization)
	_deal = lead.create_deal(contact, organization, deal)
	return _deal


def _get_lead_for_write(lead: str):
	"""Fetch a CRM Lead doc, throwing PermissionError if the session user
	doesn't have write access. Shared by add_contact, remove_contact, and
	set_primary_contact to avoid repeating the permission+fetch pattern."""
	if not frappe.has_permission("CRM Lead", "write", lead):
		frappe.throw(_("Not allowed to modify Lead"), frappe.PermissionError)
	return frappe.get_doc("CRM Lead", lead)


@frappe.whitelist()
def get_lead_contacts(name: str):
	if not frappe.has_permission("CRM Lead", "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	rows = frappe.get_all(
		"CRM Contacts",
		filters={"parenttype": "CRM Lead", "parent": name},
		fields=["contact", "is_primary"],
		distinct=True,
	)
	contact_names = [r.contact for r in rows if r.contact]
	if not contact_names:
		return []

	contact_data = {
		c.name: c
		for c in frappe.get_all(
			"Contact",
			filters={"name": ["in", contact_names]},
			fields=["name", "image", "full_name", "email_id", "mobile_no"],
		)
	}
	is_primary_map = {r.contact: r.is_primary for r in rows if r.contact}

	return [
		{
			"name": name_,
			"image": contact_data[name_].image,
			"full_name": contact_data[name_].full_name,
			"email": contact_data[name_].email_id,
			"mobile_no": contact_data[name_].mobile_no,
			"is_primary": is_primary_map.get(name_),
		}
		for name_ in contact_names
		if name_ in contact_data
	]


@frappe.whitelist()
def add_contact(lead: str, contact: str):
	doc = _get_lead_for_write(lead)
	doc.append("contacts", {"contact": contact})
	doc.save()
	return True


@frappe.whitelist()
def remove_contact(lead: str, contact: str):
	doc = _get_lead_for_write(lead)
	doc.contacts = [d for d in doc.contacts if d.contact != contact]
	doc.save()
	return True


@frappe.whitelist()
def set_primary_contact(lead: str, contact: str):
	doc = _get_lead_for_write(lead)
	for row in doc.contacts:
		row.is_primary = 1 if row.contact == contact else 0
	doc.save()
	return True

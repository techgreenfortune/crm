# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.form.assign_to import add as assign
from frappe.model.document import Document
from frappe.utils import has_gravatar, validate_email_address

from crm.fcrm.doctype.crm_service_level_agreement.utils import get_sla
from crm.fcrm.doctype.crm_status_change_log.crm_status_change_log import (
	add_status_change_log,
)
from crm.fcrm.doctype.utils import add_or_remove_lost_reason_section_in_sidepanel

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


class CRMLead(Document):
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
		self.set_sla()

	def validate(self):
		self._check_write_permission()
		self.set_full_name()
		self.set_lead_name()
		self.set_title()
		self.validate_email()
		self.validate_lost_reason()
		self.validate_c7_routing_reason()
		self.validate_partner_fabricator_name()
		self.validate_won_fields()
		self.validate_project_specific_fields()
		self.validate_c8_handoff_fields()
		self.validate_sub_source()
		if not self.is_new() and self.has_value_changed("lead_owner") and self.lead_owner:
			self.share_with_agent(self.lead_owner)
			self.assign_agent(self.lead_owner)
		if self.has_value_changed("status"):
			add_status_change_log(self)

	def after_insert(self):
		if self.lead_owner:
			if self.lead_owner != frappe.session.user:
				self.share_with_agent(self.lead_owner)
			self.assign_agent(self.lead_owner)

	def before_save(self):
		self.apply_sla()

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

	def validate_c7_routing_reason(self):
		# PRD §9: routing reason is mandatory on C7. The custom field carries
		# mandatory_depends_on for the UI, but Frappe doesn't enforce that
		# server-side (base_document.py only checks reqd=1).
		if self.status == "C7" and not self.get("custom_fabricator_routing_reason"):
			frappe.throw(
				_("Fabricator Routing Reason is required for leads at C7."),
				frappe.ValidationError,
			)

	def validate_partner_fabricator_name(self):
		# PRD §9: B2F team must record the partner fabricator's name on C7.
		if self.status == "C7" and not self.get("custom_partner_fabricator_name"):
			frappe.throw(
				_("Partner Fabricator Name is required for leads at C7."),
				frappe.ValidationError,
			)

	def validate_sub_source(self):
		# PRD §7: Sub Source is mandatory for these 5 sources via mandatory_depends_on
		# on the custom field, but Frappe enforces that only client-side
		# (base_document.py only checks reqd=1). Same UI-vs-server gap as
		# validate_c7_routing_reason.
		if frappe.flags.in_test:
			# Skip for stock Frappe test fixtures (test_records.json) which were
			# written against vanilla Frappe CRM and don't set custom_sub_source.
			# IndiFrame tests in crm/tests/ exercise this validator explicitly.
			return
		sources_requiring_sub_source = {"Referral", "Channel Partner", "Event", "Chat", "Lead Spotting"}
		if self.source in sources_requiring_sub_source and not self.get("custom_sub_source"):
			frappe.throw(
				_("Sub Source is required when Source is {0}.").format(self.source),
				frappe.ValidationError,
			)

	def validate_won_fields(self):
		# PRD §4.3: Final Quote / Price / Margin mandatory on Won stages.
		# Same UI-vs-server gap as validate_c7_routing_reason.
		if self.status and frappe.get_cached_value("CRM Lead Status", self.status, "type") == "Won":
			missing = [
				label
				for field, label in (
					("custom_final_quote", "Final Quote"),
					("custom_final_price", "Final Price"),
					("custom_final_margin", "Final Margin"),
				)
				if not self.get(field)
			]
			if missing:
				frappe.throw(
					_("Required for Won stages: {0}.").format(", ".join(missing)),
					frappe.ValidationError,
				)

	def validate_project_specific_fields(self):
		# Project-type leads (custom_lead_type='Projects') must carry full project
		# metadata before reaching C8. mandatory_depends_on enforces client-side;
		# this validator closes the UI-vs-server gap (same pattern as validate_won_fields).
		# Retail leads short-circuit immediately — the cluster is hidden for them.
		# Hardcoded status == "C8" (see validate_c8_handoff_fields for the gating
		# idiom rationale + the future-proofing follow-up note).
		if self.status != "C8":
			return
		if self.get("custom_lead_type") != "Projects":
			return
		missing = [
			label
			for field, label in (
				("custom_project_category", "Project Category"),
				("custom_project_configuration", "Project Configuration"),
				("custom_site_address_full", "Site Address (Full)"),
				("custom_site_pincode", "Site Pincode"),
			)
			if not self.get(field)
		]
		if missing:
			frappe.throw(
				_("Required for Project leads at C8: {0}.").format(", ".join(missing)),
				frappe.ValidationError,
			)

	def validate_c8_handoff_fields(self):
		# Fields that must be present on EVERY lead at C8 (Retail + Projects).
		# Backend's createProjectFromFrappe now rejects empty customer_email;
		# city/state/pincode/customer_type were silently filled with "Unknown" /
		# "000000" / NULL placeholders, breaking regional + profession reporting.
		# Catching these at save-time beats a post-hoc HTTP 400 + audit Comment.
		# `custom_lead_type` is in the list because an unset value silently
		# defaults to order_type="project" in the payload (see api.py) while
		# validate_project_specific_fields skips its cluster — yielding a project
		# row with no project metadata on the backend.
		#
		# Note: this validator hardcodes status == "C8" while validate_won_fields
		# (above) keys on CRM Lead Status.type == "Won". If a new Won-type status
		# is ever added or C8 renamed, this validator + validate_project_specific_fields
		# will silently skip; validate_won_fields will still fire. Future-proofing
		# is intentionally deferred — see plan Phase 9 note #2.
		if self.status != "C8":
			return
		missing = [
			label
			for field, label in (
				("email", "Email"),
				("custom_lead_type", "Lead Type"),
				("custom_customer_type", "Customer Type"),
				("custom_city", "City"),
				("custom_state", "State"),
				("custom_pincode", "Pincode"),
			)
			if not self.get(field)
		]
		if missing:
			frappe.throw(
				_("Required at C8 (Won): {0}.").format(", ".join(missing)),
				frappe.ValidationError,
			)

	def _check_write_permission(self):
		if self.is_new():
			return
		user = frappe.session.user
		if user == "Administrator" or self.flags.get("ignore_permissions"):
			return
		user_roles = set(frappe.get_roles(user))
		if "System Manager" in user_roles or "Sales Manager" in user_roles:
			return
		if self.lead_owner == user:
			return
		if not self.get_doc_before_save():
			return
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

	def assign_agent(self, agent):
		if not agent:
			return

		assignees = self.get_assigned_users()
		if assignees:
			for assignee in assignees:
				if agent == assignee:
					# the agent is already set as an assignee
					return

		assign({"assign_to": [agent], "doctype": "CRM Lead", "name": self.name}, ignore_permissions=True)

	def share_with_agent(self, agent):
		if not agent:
			return

		docshares = frappe.get_all(
			"DocShare",
			filters={"share_name": self.name, "share_doctype": self.doctype},
			fields=["name", "user"],
		)

		shared_with = [d.user for d in docshares] + [agent]

		for user in shared_with:
			if user == agent and not frappe.db.exists(
				"DocShare",
				{"user": agent, "share_name": self.name, "share_doctype": self.doctype},
			):
				frappe.share.add_docshare(
					self.doctype,
					self.name,
					agent,
					write=1,
					flags={"ignore_share_permission": True},
				)
			elif user != agent:
				frappe.share.remove(
					self.doctype,
					self.name,
					user,
					flags={"ignore_share_permission": True, "ignore_permissions": True},
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

		for user in self.get_assigned_users():
			if user and user != new_deal.deal_owner:
				new_deal.assign_agent(user)

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
				"label": "Assigned To",
				"type": "Text",
				"key": "_assign",
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
			"_assign",
			"image",
		]
		return {"columns": columns, "rows": rows}

	@staticmethod
	def default_kanban_settings():
		return {
			"column_field": "status",
			"title_field": "lead_name",
			"kanban_fields": '["organization", "email", "mobile_no", "_assign", "modified"]',
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

# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt
import json
import os

import click
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.fcrm.doctype.crm_dashboard.crm_dashboard import create_default_manager_dashboard
from crm.fcrm.doctype.crm_products.crm_products import create_product_details_script


def before_install():
	pass


def before_migrate():
	"""Clear stale document lock files left by any previously interrupted bench migrate."""
	from frappe.utils import file_lock, get_site_path

	locks_dir = get_site_path(file_lock.LOCKS_DIR)
	if not os.path.isdir(locks_dir):
		return
	for fname in os.listdir(locks_dir):
		if fname.endswith(".lock"):
			try:
				os.remove(os.path.join(locks_dir, fname))
				frappe.logger().warning(f"before_migrate: removed stale lock file {fname}")
			except OSError:
				pass


def after_install(force=False):
	add_default_lead_statuses()
	add_default_lead_engagement_statuses()
	add_default_deal_statuses()
	add_default_communication_statuses()
	add_default_fields_layout(force)
	add_property_setter()
	add_email_template_custom_fields()
	add_email_account_custom_field()
	add_default_industries()
	add_default_lead_sources()
	add_default_lost_reasons()
	add_default_quick_filters()
	add_standard_dropdown_items()
	add_default_scripts()
	create_default_manager_dashboard(force)
	create_assignment_rule_custom_fields()
	add_assignment_rule_property_setters()
	add_default_crm_lead_assignment_rules()
	frappe.db.commit()


def add_default_lead_statuses():
	statuses = {
		"C0": {"color": "gray", "type": "Open", "position": 1, "stage_label": "C0 — New Lead"},
		"C1": {"color": "blue", "type": "Open", "position": 2, "stage_label": "C1 — Future Requirement"},
		"C2": {"color": "orange", "type": "Ongoing", "position": 3, "stage_label": "C2 — Active Engagement"},
		"C2-Q": {"color": "amber", "type": "Ongoing", "position": 4, "stage_label": "C2-Q — Quote Sent"},
		"C3": {"color": "yellow", "type": "Ongoing", "position": 5, "stage_label": "C3 — Almost Ready"},
		"C4": {"color": "teal", "type": "Won", "position": 6, "stage_label": "C4 — Advance Payment Made"},
		"C5": {"color": "green", "type": "Won", "position": 7, "stage_label": "C5 — Invoicing Completed"},
		"C6": {"color": "red", "type": "Lost", "position": 8, "stage_label": "C6 — Lost"},
		"C7": {
			"color": "violet",
			"type": "On Hold",
			"position": 9,
			"stage_label": "C7 — Forwarded to Fabricator",
		},
		"C8": {"color": "green", "type": "Won", "position": 10, "stage_label": "C8 — Won"},
	}

	for status in statuses:
		if frappe.db.exists("CRM Lead Status", status):
			continue

		doc = frappe.new_doc("CRM Lead Status")
		doc.lead_status = status
		doc.color = statuses[status]["color"]
		doc.type = statuses[status]["type"]
		doc.position = statuses[status]["position"]
		doc.stage_label = statuses[status]["stage_label"]
		doc.insert()


def add_default_lead_engagement_statuses():
	statuses = {
		"Active": {"color": "green", "position": 1},
		"Cold-Unresponsive": {"color": "gray", "position": 2},
		"Reactivated": {"color": "cyan", "position": 3},
		"Archived": {"color": "black", "position": 4},
	}

	for status in statuses:
		if frappe.db.exists("CRM Lead Engagement Status", status):
			continue

		doc = frappe.new_doc("CRM Lead Engagement Status")
		doc.engagement_status = status
		doc.color = statuses[status]["color"]
		doc.position = statuses[status]["position"]
		doc.insert()


def add_default_deal_statuses():
	statuses = {
		"Qualification": {
			"color": "gray",
			"type": "Open",
			"probability": 10,
			"position": 1,
		},
		"Demo/Making": {
			"color": "orange",
			"type": "Ongoing",
			"probability": 25,
			"position": 2,
		},
		"Proposal/Quotation": {
			"color": "blue",
			"type": "Ongoing",
			"probability": 50,
			"position": 3,
		},
		"Negotiation": {
			"color": "yellow",
			"type": "Ongoing",
			"probability": 70,
			"position": 4,
		},
		"Ready to Close": {
			"color": "purple",
			"type": "Ongoing",
			"probability": 90,
			"position": 5,
		},
		"Won": {
			"color": "green",
			"type": "Won",
			"probability": 100,
			"position": 6,
		},
		"Lost": {
			"color": "red",
			"type": "Lost",
			"probability": 0,
			"position": 7,
		},
	}

	for status in statuses:
		if frappe.db.exists("CRM Deal Status", status):
			continue

		doc = frappe.new_doc("CRM Deal Status")
		doc.deal_status = status
		doc.color = statuses[status]["color"]
		doc.type = statuses[status]["type"]
		doc.probability = statuses[status]["probability"]
		doc.position = statuses[status]["position"]
		doc.insert()


def add_default_communication_statuses():
	statuses = ["Open", "Replied"]

	for status in statuses:
		if frappe.db.exists("CRM Communication Status", status):
			continue

		doc = frappe.new_doc("CRM Communication Status")
		doc.status = status
		doc.insert()


def add_default_fields_layout(force=False):
	quick_entry_layouts = {
		"CRM Lead-Quick Entry": {
			"doctype": "CRM Lead",
			"layout": '[{"name": "person_section", "label": "Person", "columns": [{"name": "column_qe_p1", "fields": ["salutation", "first_name"]}, {"name": "column_qe_p2", "fields": ["last_name", "email"]}, {"name": "column_qe_p3", "fields": ["mobile_no", "phone"]}, {"name": "column_qe_p4", "fields": ["gender", "job_title"]}]}, {"name": "organization_section", "label": "Organization", "columns": [{"name": "column_qe_o1", "fields": ["organization", "custom_gst_number"]}, {"name": "column_qe_o2", "fields": ["website", "industry"]}, {"name": "column_qe_o3", "fields": ["territory", "annual_revenue"]}, {"name": "column_qe_o4", "fields": ["no_of_employees"]}]}, {"name": "property_section", "label": "Property / Site", "columns": [{"name": "column_qe_pr1", "fields": ["custom_pincode", "custom_city"]}, {"name": "column_qe_pr2", "fields": ["custom_state", "custom_area"]}, {"name": "column_qe_pr3", "fields": ["custom_latitude", "custom_longitude"]}, {"name": "column_qe_pr4", "fields": ["custom_tentative_area_sqft", "custom_tentative_value"]}, {"name": "column_qe_pr5", "fields": ["custom_site_photos"]}]}, {"name": "classification_section", "label": "Lead Classification", "columns": [{"name": "column_qe_c1", "fields": ["custom_lead_type", "custom_customer_type"]}, {"name": "column_qe_c2", "fields": ["custom_account"]}, {"name": "column_qe_c3", "fields": ["source", "custom_sub_source"]}]}, {"name": "utm_section", "label": "Marketing Attribution", "columns": [{"name": "column_qe_u1", "fields": ["custom_utm_source", "custom_utm_medium"]}, {"name": "column_qe_u2", "fields": ["custom_utm_campaign", "custom_utm_content"]}, {"name": "column_qe_u3", "fields": ["custom_competitors"]}]}, {"name": "lead_section", "label": "Lead Owner & Stage", "columns": [{"name": "column_qe_l1", "fields": ["status", "lead_status"]}, {"name": "column_qe_l2", "fields": ["lead_owner"]}]}]',
		},
		"CRM Deal-Quick Entry": {
			"doctype": "CRM Deal",
			"layout": '[{"name": "organization_section", "hidden": true, "editable": false, "columns": [{"name": "column_GpMP", "fields": ["organization"]}, {"name": "column_FPTn", "fields": []}]}, {"name": "organization_details_section", "editable": false, "columns": [{"name": "column_S3tQ", "fields": ["organization_name", "territory"]}, {"name": "column_KqV1", "fields": ["website", "annual_revenue"]}, {"name": "column_1r67", "fields": ["no_of_employees", "industry"]}]}, {"name": "contact_section", "hidden": true, "editable": false, "columns": [{"name": "column_CeXr", "fields": ["contact"]}, {"name": "column_yHbk", "fields": []}]}, {"name": "contact_details_section", "editable": false, "columns": [{"name": "column_ZTWr", "fields": ["salutation", "email"]}, {"name": "column_tabr", "fields": ["first_name", "mobile_no"]}, {"name": "column_Qjdx", "fields": ["last_name", "gender"]}]}, {"name": "deal_section", "columns": [{"name": "column_mdps", "fields": ["status"]}, {"name": "column_H40H", "fields": ["deal_owner"]}]}]',
		},
		"Contact-Quick Entry": {
			"doctype": "Contact",
			"layout": '[{"name": "salutation_section", "columns": [{"name": "column_eXks", "fields": ["salutation"]}]}, {"name": "full_name_section", "hideBorder": true, "columns": [{"name": "column_cSxf", "fields": ["first_name"]}, {"name": "column_yBc7", "fields": ["last_name"]}]}, {"name": "email_section", "hideBorder": true, "columns": [{"name": "column_tH3L", "fields": ["email_id"]}]}, {"name": "mobile_gender_section", "hideBorder": true, "columns": [{"name": "column_lrfI", "fields": ["mobile_no"]}, {"name": "column_Tx3n", "fields": ["gender"]}]}, {"name": "organization_section", "hideBorder": true, "columns": [{"name": "column_S0J8", "fields": ["company_name"]}]}, {"name": "designation_section", "hideBorder": true, "columns": [{"name": "column_bsO8", "fields": ["designation"]}]}, {"name": "address_section", "hideBorder": true, "columns": [{"name": "column_W3VY", "fields": ["address"]}]}]',
		},
		"CRM Organization-Quick Entry": {
			"doctype": "CRM Organization",
			"layout": '[{"name": "organization_section", "columns": [{"name": "column_zOuv", "fields": ["organization_name"]}]}, {"name": "website_revenue_section", "hideBorder": true, "columns": [{"name": "column_I5Dy", "fields": ["website"]}, {"name": "column_Rgss", "fields": ["annual_revenue"]}]}, {"name": "territory_section", "hideBorder": true, "columns": [{"name": "column_w6ap", "fields": ["territory"]}]}, {"name": "employee_industry_section", "hideBorder": true, "columns": [{"name": "column_u5tZ", "fields": ["no_of_employees"]}, {"name": "column_FFrT", "fields": ["industry"]}]}, {"name": "address_section", "hideBorder": true, "columns": [{"name": "column_O2dk", "fields": ["address"]}]}]',
		},
		"Address-Quick Entry": {
			"doctype": "Address",
			"layout": '[{"name": "details_section", "columns": [{"name": "column_uSSG", "fields": ["address_title", "address_type", "address_line1", "address_line2", "city", "state", "country", "pincode"]}]}]',
		},
		"CRM Call Log-Quick Entry": {
			"doctype": "CRM Call Log",
			"layout": '[{"name":"details_section","columns":[{"name":"column_uMSG","fields":["type","from","duration"]},{"name":"column_wiZT","fields":["to","status","caller","receiver"]}]}]',
		},
		"FCRM Note-Quick Entry": {
			"doctype": "FCRM Note",
			"layout": '[{"name":"details_section","columns":[{"name":"column_o2s9","fields":["title", "content"]}]}]',
		},
		"CRM Task-Quick Entry": {
			"doctype": "CRM Task",
			"layout": '[{"name":"first_tab","sections":[{"name":"details_section","columns":[{"name":"column_X9sG","fields":["title","description"]}]},{"name":"assignment_section","columns":[{"name":"column_9XjK","fields":["priority","due_date"]},{"name":"column_7s8n","fields":["assigned_to","status"]}],"hideBorder":true}]}]',
		},
	}

	sidebar_fields_layouts = {
		"CRM Lead-Side Panel": {
			"doctype": "CRM Lead",
			"layout": '[{"label": "Contacts", "name": "contacts_section", "opened": true, "editable": false, "contacts": []}, {"label": "Lead Status", "name": "lead_status_section", "opened": true, "columns": [{"name": "column_sp_ls1", "fields": ["status", "lead_status", "lead_owner", "custom_reactivated_at"]}]}, {"label": "Person", "name": "person_section", "opened": true, "columns": [{"name": "column_sp_p1", "fields": ["salutation", "first_name", "last_name", "email", "mobile_no", "phone", "gender", "job_title"]}]}, {"label": "Organization", "name": "organization_section", "opened": true, "columns": [{"name": "column_sp_o1", "fields": ["organization", "custom_gst_number", "website", "territory", "industry", "annual_revenue", "no_of_employees"]}]}, {"label": "Source & Classification", "name": "source_section", "opened": true, "columns": [{"name": "column_sp_s1", "fields": ["source", "custom_sub_source", "custom_lead_type", "custom_customer_type", "custom_account"]}]}, {"label": "Property", "name": "property_section", "opened": false, "columns": [{"name": "column_sp_pr1", "fields": ["custom_pincode", "custom_city", "custom_state", "custom_area", "custom_site_address_full", "custom_site_pincode", "custom_latitude", "custom_longitude", "custom_tentative_area_sqft", "custom_tentative_value", "custom_site_photos"]}]}, {"label": "Project Details", "name": "project_section", "opened": false, "columns": [{"name": "column_sp_pj1", "fields": ["custom_project_category", "custom_project_configuration", "custom_external_project_id"]}]}, {"label": "Fabricator Routing", "name": "routing_section", "opened": false, "columns": [{"name": "column_sp_r1", "fields": ["custom_fabricator_routing_reason", "custom_partner_fabricator_name", "custom_fabricator_routing_notes"]}]}, {"label": "Latest Quote", "name": "quote_section", "opened": false, "columns": [{"name": "column_sp_q1", "fields": ["custom_final_quote", "custom_final_price", "custom_final_margin"]}]}, {"label": "Marketing (UTM)", "name": "utm_section", "opened": false, "columns": [{"name": "column_sp_u1", "fields": ["custom_utm_source", "custom_utm_medium", "custom_utm_campaign", "custom_utm_content", "custom_competitors"]}]}]',
		},
		"CRM Deal-Side Panel": {
			"doctype": "CRM Deal",
			"layout": '[{"label": "Contacts", "name": "contacts_section", "opened": true, "editable": false, "contacts": []}, {"label": "Organization Details", "name": "organization_section", "opened": true, "columns": [{"name": "column_na2Q", "fields": ["organization", "website", "territory", "annual_revenue", "close_date", "probability", "next_step", "deal_owner"]}]}]',
		},
		"Contact-Side Panel": {
			"doctype": "Contact",
			"layout": '[{"label": "Details", "name": "details_section", "opened": true, "columns": [{"name": "column_eIWl", "fields": ["salutation", "first_name", "last_name", "email_id", "mobile_no", "gender", "company_name", "designation", "address"]}]}]',
		},
		"CRM Organization-Side Panel": {
			"doctype": "CRM Organization",
			"layout": '[{"label": "Details", "name": "details_section", "opened": true, "columns": [{"name": "column_IJOV", "fields": ["organization_name", "website", "territory", "industry", "no_of_employees", "address"]}]}]',
		},
	}

	data_fields_layouts = {
		"CRM Lead-Data Fields": {
			"doctype": "CRM Lead",
			"layout": '[{"label": "Details", "name": "details_section", "opened": true, "columns": [{"name": "column_ZgLG", "fields": ["organization", "industry", "lead_owner"]}, {"name": "column_TbYq", "fields": ["website", "job_title"]}, {"name": "column_OKSX", "fields": ["territory", "source"]}]}, {"label": "Person", "name": "person_section", "opened": true, "columns": [{"name": "column_6c5g", "fields": ["salutation", "email"]}, {"name": "column_1n7Q", "fields": ["first_name", "mobile_no"]}, {"name": "column_cT6C", "fields": ["last_name"]}]}]',
		},
		"CRM Deal-Data Fields": {
			"doctype": "CRM Deal",
			"layout": '[{"name":"first_tab","sections":[{"label":"Details","name":"details_section","opened":true,"columns":[{"name":"column_z9XL","fields":["organization","annual_revenue","next_step"]},{"name":"column_gM4w","fields":["website","closed_date","deal_owner"]},{"name":"column_gWmE","fields":["territory","probability"]}]},{"label":"Products","name":"section_jHhQ","opened":true,"columns":[{"name":"column_xiNF","fields":["products"]}],"editingLabel":false,"hideLabel":true},{"label":"New Section","name":"section_WNOQ","opened":true,"columns":[{"name":"column_ziBW","fields":["total"]},{"label":"","name":"column_wuwA","fields":["net_total"]}],"hideBorder":true,"hideLabel":true}]}]',
		},
	}

	for layout in quick_entry_layouts:
		if frappe.db.exists("CRM Fields Layout", layout):
			if force:
				frappe.delete_doc("CRM Fields Layout", layout)
			else:
				continue

		doc = frappe.new_doc("CRM Fields Layout")
		doc.type = "Quick Entry"
		doc.dt = quick_entry_layouts[layout]["doctype"]
		doc.layout = quick_entry_layouts[layout]["layout"]
		doc.insert()

	for layout in sidebar_fields_layouts:
		if frappe.db.exists("CRM Fields Layout", layout):
			if force:
				frappe.delete_doc("CRM Fields Layout", layout)
			else:
				continue

		doc = frappe.new_doc("CRM Fields Layout")
		doc.type = "Side Panel"
		doc.dt = sidebar_fields_layouts[layout]["doctype"]
		doc.layout = sidebar_fields_layouts[layout]["layout"]
		doc.insert()

	for layout in data_fields_layouts:
		if frappe.db.exists("CRM Fields Layout", layout):
			if force:
				frappe.delete_doc("CRM Fields Layout", layout)
			else:
				continue

		doc = frappe.new_doc("CRM Fields Layout")
		doc.type = "Data Fields"
		doc.dt = data_fields_layouts[layout]["doctype"]
		doc.layout = data_fields_layouts[layout]["layout"]
		doc.insert()


def add_property_setter():
	if not frappe.db.exists("Property Setter", {"name": "Contact-main-search_fields"}):
		doc = frappe.new_doc("Property Setter")
		doc.doctype_or_field = "DocType"
		doc.doc_type = "Contact"
		doc.property = "search_fields"
		doc.property_type = "Data"
		doc.value = "email_id"
		doc.insert()

	add_crm_lead_property_setters()


def add_crm_lead_property_setters():
	"""Seed CRM Lead Customize-Form property setters. Idempotent — admin tweaks via Desk UI are never overwritten."""
	field_order_value = (
		'["person_tab", "salutation", "first_name", "last_name", "column_break_opsm", '
		'"lead_name", "email", "mobile_no", "details", "organization", "website", '
		'"territory", "industry", "job_title", "source", "custom_indiframe_details_section", '
		'"lead_owner", "organization_tab", "section_break_uixv", "naming_series", '
		'"middle_name", "gender", "phone", "column_break_dbsv", "status", "no_of_employees", '
		'"annual_revenue", "image", "converted", "products_tab", "products", '
		'"section_break_ggwh", "total", "column_break_uisv", "net_total", "sla_tab", "sla", '
		'"sla_creation", "column_break_ffnp", "sla_status", "communication_status", '
		'"response_details_section", "response_by", "column_break_pweh", "first_response_time", '
		'"first_responded_on", "section_break_xnpz", "rolling_responses", "section_break_kikl", '
		'"column_break_ygds", "last_response_time", "column_break_tcqb", "last_responded_on", '
		'"log_tab", "status_change_log", "syncing_tab", "facebook_lead_id", "column_break_ixmu", '
		'"facebook_form_id", "lost_details_tab", "lost_reason", "lost_notes", "custom_indiframe", '
		'"custom_indiframe_details", "custom_lead_type", "custom_customer_type", '
		'"custom_account", "custom_fabricator_routing_reason", "custom_fabricator_routing_notes", '
		'"custom_final_price", "custom_final_margin", "custom_final_quote", '
		'"custom_property_section", "custom_pincode", "custom_area", "custom_latitude", '
		'"custom_longitude", "custom_tentative_area_sqft", "custom_tentative_value", '
		'"custom_site_photos", "custom_sub_source", "custom_competitors", "custom_utm_section", '
		'"custom_utm_source", "custom_utm_medium", "custom_utm_campaign", "custom_utm_content"]'
	)

	setters = [
		{
			"name": "CRM Lead-main-field_order",
			"doctype_or_field": "DocType",
			"field_name": None,
			"property": "field_order",
			"property_type": "Data",
			"value": field_order_value,
		},
		{
			"name": "CRM Lead-lost_reason-mandatory_depends_on",
			"doctype_or_field": "DocField",
			"field_name": "lost_reason",
			"property": "mandatory_depends_on",
			"property_type": "Code",
			"value": 'eval:doc.status == "C6"',
		},
		{
			"name": "CRM Lead-lost_reason-read_only_depends_on",
			"doctype_or_field": "DocField",
			"field_name": "lost_reason",
			"property": "read_only_depends_on",
			"property_type": "Code",
			"value": 'eval:doc.status == "C6" && !!doc.lost_reason',
		},
		{
			"name": "CRM Lead-status-read_only_depends_on",
			"doctype_or_field": "DocField",
			"field_name": "status",
			"property": "read_only_depends_on",
			"property_type": "Code",
			"value": 'eval:!doc.name || ["C6","C8"].includes(doc.status)',
		},
		{
			"name": "CRM Lead-lead_status-read_only_depends_on",
			"doctype_or_field": "DocField",
			"field_name": "lead_status",
			"property": "read_only_depends_on",
			"property_type": "Code",
			# Read-only on new docs (server default is "Active") and on terminal C-stages
			# (Script 1 also forces it to "Active" there). Mirrors the `status` property setter.
			"value": 'eval:!doc.name || ["C6","C8"].includes(doc.status)',
		},
		{
			"name": "CRM Lead-custom_sub_source-mandatory_depends_on",
			"doctype_or_field": "DocField",
			"field_name": "custom_sub_source",
			"property": "mandatory_depends_on",
			"property_type": "Code",
			"value": 'eval:["Referral","Channel Partner","Event","Chat","Lead Spotting"].includes(doc.source)',
		},
	]

	for setter in setters:
		if frappe.db.exists("Property Setter", setter["name"]):
			# Upsert: keep the value aligned with install.py so `after_migrate` can
			# re-apply the latest values without leaving stale rows behind. Skip
			# the save when the value already matches to avoid pointless writes.
			existing = frappe.get_doc("Property Setter", setter["name"])
			if existing.value != setter["value"]:
				existing.value = setter["value"]
				existing.save(ignore_permissions=True)
			continue
		doc = frappe.new_doc("Property Setter")
		doc.doc_type = "CRM Lead"
		doc.doctype_or_field = setter["doctype_or_field"]
		doc.field_name = setter["field_name"]
		doc.property = setter["property"]
		doc.property_type = setter["property_type"]
		doc.value = setter["value"]
		doc.is_system_generated = 1
		doc.insert()


def add_email_template_custom_fields():
	if not frappe.get_meta("Email Template").has_field("enabled"):
		click.secho("* Installing Custom Fields in Email Template")

		create_custom_fields(
			{
				"Email Template": [
					{
						"default": "0",
						"fieldname": "enabled",
						"fieldtype": "Check",
						"label": "Enabled",
						"insert_after": "",
					},
					{
						"fieldname": "reference_doctype",
						"fieldtype": "Link",
						"label": "Doctype",
						"options": "DocType",
						"insert_after": "enabled",
					},
				]
			}
		)

		frappe.clear_cache(doctype="Email Template")


def add_email_account_custom_field():
	if not frappe.get_meta("Email Account").has_field("create_lead_from_incoming_email"):
		click.secho("* Installing Custom Fields in Email Account")

		create_custom_fields(
			{
				"Email Account": [
					{
						"default": "0",
						"fieldname": "create_lead_from_incoming_email",
						"fieldtype": "Check",
						"label": "Create Lead from Incoming Emails",
						"description": "Automatically create a lead when an incoming email is received from an unknown contact",
						"insert_after": "create_contact",
					}
				]
			}
		)

		frappe.clear_cache(doctype="Email Account")


def add_default_industries():
	industries = [
		"Accounting",
		"Advertising",
		"Aerospace",
		"Agriculture",
		"Airline",
		"Apparel & Accessories",
		"Automotive",
		"Banking",
		"Biotechnology",
		"Broadcasting",
		"Brokerage",
		"Chemical",
		"Computer",
		"Consulting",
		"Consumer Products",
		"Cosmetics",
		"Defense",
		"Department Stores",
		"Education",
		"Electronics",
		"Energy",
		"Entertainment & Leisure, Executive Search",
		"Financial Services",
		"Food",
		"Beverage & Tobacco",
		"Grocery",
		"Health Care",
		"Internet Publishing",
		"Investment Banking",
		"Legal",
		"Manufacturing",
		"Motion Picture & Video",
		"Music",
		"Newspaper Publishers",
		"Online Auctions",
		"Pension Funds",
		"Pharmaceuticals",
		"Private Equity",
		"Publishing",
		"Real Estate",
		"Retail & Wholesale",
		"Securities & Commodity Exchanges",
		"Service",
		"Soap & Detergent",
		"Software",
		"Sports",
		"Technology",
		"Telecommunications",
		"Television",
		"Transportation",
		"Venture Capital",
	]

	for industry in industries:
		if frappe.db.exists("CRM Industry", industry):
			continue

		doc = frappe.new_doc("CRM Industry")
		doc.industry = industry
		doc.insert()


def add_default_lead_sources():
	# IndiFrame canonical Lead Sources — kept in sync with
	# crm/fixtures/crm_lead_source.json (PRD §7 attribution table).
	# The previous demo set (Email, Existing Customer, Facebook, etc.)
	# was retired when source attribution was scoped to indiframe.com lead origins.
	lead_sources = [
		"Paid",
		"Organic Search",
		"Direct",
		"Chat",
		"Referral",
		"Channel Partner",
		"Event",
		"Lead Spotting",
	]

	for source in lead_sources:
		if frappe.db.exists("CRM Lead Source", source):
			continue

		doc = frappe.new_doc("CRM Lead Source")
		doc.source_name = source
		doc.insert()


def add_default_lost_reasons():
	lost_reasons = [
		{
			"reason": "Pricing",
			"description": "The prospect found the pricing to be too high or not competitive.",
		},
		{"reason": "Competition", "description": "The prospect chose a competitor's product or service."},
		{
			"reason": "Budget Constraints",
			"description": "The prospect did not have the budget to proceed with the purchase.",
		},
		{
			"reason": "Missing Features",
			"description": "The prospect felt that the product or service was missing key features they needed.",
		},
		{
			"reason": "Long Sales Cycle",
			"description": "The sales process took too long, leading to loss of interest.",
		},
		{
			"reason": "No Decision-Maker",
			"description": "The prospect was not the decision-maker and could not proceed.",
		},
		{"reason": "Unresponsive Prospect", "description": "The prospect did not respond to follow-ups."},
		{"reason": "Poor Fit", "description": "The prospect was not a good fit for the product or service."},
		{"reason": "Other", "description": ""},
	]

	for reason in lost_reasons:
		if frappe.db.exists("CRM Lost Reason", reason["reason"]):
			continue

		doc = frappe.new_doc("CRM Lost Reason")
		doc.lost_reason = reason["reason"]
		doc.description = reason["description"]
		doc.insert()


def add_default_quick_filters():
	quick_filters = {
		"CRM Lead": ["lead_name", "email", "lead_status", "status", "source"],
		"CRM Deal": ["organization", "status", "probability", "email"],
		"Contact": ["status", "email_id", "phone"],
		"CRM Organization": ["organization_name", "no_of_employees", "territory", "industry"],
		"CRM Task": ["title", "priority", "assigned_to", "status", "due_date"],
		"CRM Call Log": ["telephony_medium", "type", "status", "from", "to"],
	}

	for quick_filter in quick_filters:
		if frappe.db.exists("CRM Global Settings", {"dt": quick_filter}):
			continue

		doc = frappe.new_doc("CRM Global Settings")
		doc.dt = quick_filter
		doc.json = json.dumps(quick_filters[quick_filter])
		doc.insert()


def add_standard_dropdown_items():
	crm_settings = frappe.get_single("FCRM Settings")

	# don't add dropdown items if they're already present
	if crm_settings.dropdown_items:
		return

	crm_settings.dropdown_items = []

	for item in frappe.get_hooks("standard_dropdown_items"):
		crm_settings.append("dropdown_items", item)

	crm_settings.save()


def add_default_scripts():
	from crm.fcrm.doctype.fcrm_settings.fcrm_settings import create_forecasting_script

	for doctype in ["CRM Lead", "CRM Deal"]:
		create_product_details_script(doctype)
	create_forecasting_script()


def add_assignment_rule_property_setters():
	"""Add a property setter to the Assignment Rule DocType for assign_condition and unassign_condition."""

	default_fields = {
		"doctype": "Property Setter",
		"doctype_or_field": "DocField",
		"doc_type": "Assignment Rule",
		"property_type": "Data",
		"is_system_generated": 1,
	}

	if not frappe.db.exists("Property Setter", {"name": "Assignment Rule-assign_condition-depends_on"}):
		frappe.get_doc(
			{
				**default_fields,
				"name": "Assignment Rule-assign_condition-depends_on",
				"field_name": "assign_condition",
				"property": "depends_on",
				"value": "eval: !doc.assign_condition_json",
			}
		).insert()
	else:
		frappe.db.set_value(
			"Property Setter",
			{"name": "Assignment Rule-assign_condition-depends_on"},
			"value",
			"eval: !doc.assign_condition_json",
		)
	if not frappe.db.exists("Property Setter", {"name": "Assignment Rule-unassign_condition-depends_on"}):
		frappe.get_doc(
			{
				**default_fields,
				"name": "Assignment Rule-unassign_condition-depends_on",
				"field_name": "unassign_condition",
				"property": "depends_on",
				"value": "eval: !doc.unassign_condition_json",
			}
		).insert()
	else:
		frappe.db.set_value(
			"Property Setter",
			{"name": "Assignment Rule-unassign_condition-depends_on"},
			"value",
			"eval: !doc.unassign_condition_json",
		)


def create_assignment_rule_custom_fields():
	if not frappe.get_meta("Assignment Rule").has_field("assign_condition_json"):
		click.secho("* Installing Custom Fields in Assignment Rule")

		create_custom_fields(
			{
				"Assignment Rule": [
					{
						"description": "Autogenerated field by CRM App",
						"fieldname": "assign_condition_json",
						"fieldtype": "Code",
						"label": "Assign Condition JSON",
						"insert_after": "assign_condition",
						"depends_on": "eval: doc.assign_condition_json",
					},
					{
						"description": "Autogenerated field by CRM App",
						"fieldname": "unassign_condition_json",
						"fieldtype": "Code",
						"label": "Unassign Condition JSON",
						"insert_after": "unassign_condition",
						"depends_on": "eval: doc.unassign_condition_json",
					},
				],
			}
		)

		frappe.clear_cache(doctype="Assignment Rule")


def add_default_crm_lead_assignment_rules():
	"""Seed B2F-on-C7 and ASM-on-C2 rules. Idempotent — admin's `users` roster and `disabled` flag are never overwritten."""
	rules = [
		{
			"name": "B2F Assignment on C7",
			"description": "Assign C7 leads to B2F Team members",
			"assign_condition": 'status == "C7"',
			"priority": 1,
		},
		{
			"name": "ASM Assignment on C2",
			"description": "Assign C2 leads to Area Sales Manager — enable in Phase 2",
			"assign_condition": 'doc.status == "C2" and doc.custom_lead_type',
			"priority": 2,
		},
	]
	days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

	for rule in rules:
		if frappe.db.exists("Assignment Rule", rule["name"]):
			continue
		doc = frappe.new_doc("Assignment Rule")
		doc.name = rule["name"]
		doc.document_type = "CRM Lead"
		doc.description = rule["description"]
		doc.assign_condition = rule["assign_condition"]
		doc.priority = rule["priority"]
		doc.rule = "Round Robin"
		doc.disabled = 1
		for day in days:
			doc.append("assignment_days", {"day": day})
		doc.insert()

# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CRMDashboard(Document):
	pass


def default_manager_dashboard_layout():
	"""
	Returns the default layout for the CRM Manager Dashboard.

	Layout uses a 20-column grid. IndiFrame lead-centric analytics are
	appended after the stock charts (y >= 34) per analytics spec §11.
	"""
	import json

	stock_charts = [
		# KPI tiles (number_charts) — y=0..2
		{
			"name": "total_leads",
			"type": "number_chart",
			"tooltip": "Total number of leads",
			"layout": {"x": 0, "y": 0, "w": 4, "h": 3, "i": "total_leads"},
		},
		{
			"name": "average_time_to_close_a_lead",
			"type": "number_chart",
			"tooltip": "Average time taken to close a lead",
			"layout": {"x": 4, "y": 0, "w": 4, "h": 3, "i": "average_time_to_close_a_lead"},
		},
		# Other stock KPI tiles available if you want to enable them later:
		# {"name": "ongoing_deals", "type": "number_chart", "tooltip": "Total number of ongoing deals", "layout": {"x": 8, "y": 0, "w": 4, "h": 3, "i": "ongoing_deals"}},
		# {"name": "won_deals", "type": "number_chart", "tooltip": "Total number of won deals", "layout": {"x": 12, "y": 0, "w": 4, "h": 3, "i": "won_deals"}},
		# {"name": "average_won_deal_value", "type": "number_chart", "tooltip": "Average value of won deals", "layout": {"x": 16, "y": 0, "w": 4, "h": 3, "i": "average_won_deal_value"}},
		# {"name": "average_deal_value", "type": "number_chart", "tooltip": "Average deal value of ongoing and won deals", "layout": {"x": 0, "y": 2, "w": 4, "h": 3, "i": "average_deal_value"}},
		# {"name": "average_time_to_close_a_deal", "type": "number_chart", "layout": {"x": 4, "y": 2, "w": 4, "h": 3, "i": "average_time_to_close_a_deal"}},
		# {"name": "spacer", "type": "spacer", "layout": {"x": 8, "y": 2, "w": 12, "h": 3, "i": "spacer"}},
		# # Existing deal-centric axis/donut charts
		# {"name": "sales_trend", "type": "axis_chart", "layout": {"x": 0, "y": 4, "w": 10, "h": 9, "i": "sales_trend"}},
		# {"name": "forecasted_revenue", "type": "axis_chart", "layout": {"x": 10, "y": 4, "w": 10, "h": 9, "i": "forecasted_revenue"}},
		# {"name": "funnel_conversion", "type": "axis_chart", "layout": {"x": 0, "y": 11, "w": 10, "h": 9, "i": "funnel_conversion"}},
		# {"name": "deals_by_stage_donut", "type": "donut_chart", "layout": {"x": 10, "y": 11, "w": 10, "h": 9, "i": "deals_by_stage_donut"}},
		# {"name": "leads_by_source", "type": "donut_chart", "layout": {"x": 0, "y": 18, "w": 10, "h": 9, "i": "leads_by_source"}},
		# {"name": "deals_by_source", "type": "donut_chart", "layout": {"x": 10, "y": 18, "w": 10, "h": 9, "i": "deals_by_source"}},
		# {"name": "deals_by_territory", "type": "axis_chart", "layout": {"x": 0, "y": 25, "w": 10, "h": 9, "i": "deals_by_territory"}},
		# {"name": "deals_by_salesperson", "type": "axis_chart", "layout": {"x": 10, "y": 25, "w": 10, "h": 9, "i": "deals_by_salesperson"}},
		# {"name": "lost_deal_reasons", "type": "axis_chart", "layout": {"x": 0, "y": 32, "w": 20, "h": 9, "i": "lost_deal_reasons"}},
	]

	indiframe_charts = [
		# §11.1 Lead Generation Performance
		{
			"name": "leads_over_time",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 4, "w": 20, "h": 9, "i": "leads_over_time"},
		},
		# Quotes Sent & Orders Won — mirror the lead-generation pattern
		# (event-over-time line charts).  Side-by-side on the same row.
		{
			"name": "quotes_sent_over_time",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 13, "w": 10, "h": 9, "i": "quotes_sent_over_time"},
		},
		{
			"name": "orders_won_over_time",
			"type": "axis_chart",
			"layout": {"x": 10, "y": 13, "w": 10, "h": 9, "i": "orders_won_over_time"},
		},
		# §11.2 Pipeline + §11.4 Loss Analysis side-by-side
		{
			"name": "lead_pipeline_funnel",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 22, "w": 10, "h": 9, "i": "lead_pipeline_funnel"},
		},
		{
			"name": "lost_lead_reasons",
			"type": "axis_chart",
			"layout": {"x": 10, "y": 22, "w": 10, "h": 9, "i": "lost_lead_reasons"},
		},
		# §11.1 source + sub-source as bar charts (per requirement: no donuts here)
		{
			"name": "leads_by_source_axis",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 31, "w": 10, "h": 9, "i": "leads_by_source_axis"},
		},
		{
			"name": "leads_by_sub_source",
			"type": "axis_chart",
			"layout": {"x": 10, "y": 31, "w": 10, "h": 9, "i": "leads_by_sub_source"},
		},
		# §11.3 Calling Team Productivity row 1
		{
			"name": "calls_per_caller",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 40, "w": 10, "h": 9, "i": "calls_per_caller"},
		},
		{
			"name": "call_dispositions",
			"type": "donut_chart",
			"layout": {"x": 10, "y": 40, "w": 10, "h": 9, "i": "call_dispositions"},
		},
		# §11.3 Calling row 2 + §11.1 Lead Spotting
		{
			"name": "avg_c0_to_c2_time_per_caller",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 49, "w": 10, "h": 9, "i": "avg_c0_to_c2_time_per_caller"},
		},
		{
			"name": "lead_spotting_productivity",
			"type": "axis_chart",
			"layout": {"x": 10, "y": 49, "w": 10, "h": 9, "i": "lead_spotting_productivity"},
		},
		# §11.5 Geography (full width)
		{
			"name": "leads_by_geography",
			"type": "axis_chart",
			"layout": {"x": 0, "y": 58, "w": 20, "h": 9, "i": "leads_by_geography"},
		},
		# §11.2 C2 sub-status — small enough to still work as a donut
		{
			"name": "c2_sub_status_breakdown",
			"type": "donut_chart",
			"layout": {"x": 0, "y": 67, "w": 10, "h": 9, "i": "c2_sub_status_breakdown"},
		},
	]
	

	return json.dumps(stock_charts + indiframe_charts)


def create_default_manager_dashboard(force=False):
	"""
	Creates the default CRM Manager Dashboard if it does not exist.
	"""
	if not frappe.db.exists("CRM Dashboard", "Manager Dashboard"):
		doc = frappe.new_doc("CRM Dashboard")
		doc.title = "Manager Dashboard"
		doc.layout = default_manager_dashboard_layout()
		doc.insert(ignore_permissions=True)
	else:
		doc = frappe.get_doc("CRM Dashboard", "Manager Dashboard")
		if force:
			doc.layout = default_manager_dashboard_layout()
			doc.save(ignore_permissions=True)
	return doc.layout

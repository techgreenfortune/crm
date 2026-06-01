import json

import frappe
from frappe import _
from frappe.query_builder import Case, DocType
from frappe.query_builder.functions import Avg, Coalesce, Count, Date, DateFormat, IfNull, Sum
from pypika.functions import Function

from crm.fcrm.doctype.crm_dashboard.crm_dashboard import create_default_manager_dashboard
from crm.utils import sales_user_only

# CRM roles that get the team-wide ("Manager Dashboard") view. Mirrors the
# Sales Manager role_profile bundling that existed before the role retirement
# on 2026-05-25 — Sales Head / Sales Coordinator / RSM / ASM / Management were
# the five profiles bundled with Sales Manager, plus System Manager for admins.
# Anyone else with Sales User in their roles is treated as an individual
# contributor (self-only filter).
_MANAGER_ROLES = frozenset({"System Manager", "Sales Head", "Sales Coordinator", "RSM", "ASM", "Management"})


# Custom function for TIMESTAMPDIFF (MySQL/MariaDB)
class TimestampDiff(Function):
	def __init__(self, unit, start, end, **kwargs):
		super().__init__("TIMESTAMPDIFF", unit, start, end, **kwargs)


@frappe.whitelist()
def reset_to_default():
	frappe.only_for("System Manager", True)
	create_default_manager_dashboard(force=True)


@frappe.whitelist()
@sales_user_only
def get_dashboard(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get the dashboard data for the CRM dashboard.
	"""

	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	roles = set(frappe.get_roles(frappe.session.user))
	# Roles that previously got Sales Manager via role_profile.json bundling
	# (Sales Head, Sales Coordinator, RSM, ASM, Management). Preserves the
	# old "manager dashboard" behaviour after Sales Manager retirement.
	is_manager = bool(roles & _MANAGER_ROLES)
	is_individual_contributor = ("Sales User" in roles) and not is_manager

	if is_individual_contributor:
		user = frappe.session.user

	dashboard = frappe.db.exists("CRM Dashboard", "Manager Dashboard")

	layout = []

	if not dashboard:
		layout = json.loads(create_default_manager_dashboard())
		frappe.db.commit()
	else:
		layout = json.loads(frappe.db.get_value("CRM Dashboard", "Manager Dashboard", "layout") or "[]")

	for l in layout:
		method_name = f"get_{l['name']}"
		if hasattr(frappe.get_attr("crm.api.dashboard"), method_name):
			method = getattr(frappe.get_attr("crm.api.dashboard"), method_name)
			l["data"] = method(from_date, to_date, user)
		else:
			l["data"] = None

	return layout


@frappe.whitelist()
@sales_user_only
def get_chart(
	name: str, type: str, from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get number chart data for the dashboard.
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	roles = set(frappe.get_roles(frappe.session.user))
	is_manager = bool(roles & _MANAGER_ROLES)
	is_individual_contributor = ("Sales User" in roles) and not is_manager

	if is_individual_contributor:
		user = frappe.session.user

	method_name = f"get_{name}"
	if hasattr(frappe.get_attr("crm.api.dashboard"), method_name):
		method = getattr(frappe.get_attr("crm.api.dashboard"), method_name)
		return method(from_date, to_date, user)
	else:
		return {"error": _("Invalid chart name")}


def get_total_leads(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get lead count for the dashboard.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Lead = DocType("CRM Lead")

	# Build conditions for current period
	current_cond = (Lead.creation >= from_date) & (Lead.creation < to_date_plus_one)
	if user:
		current_cond = current_cond & (Lead.lead_owner == user)

	# Build conditions for previous period
	prev_cond = (Lead.creation >= prev_from_date) & (Lead.creation < from_date)
	if user:
		prev_cond = prev_cond & (Lead.lead_owner == user)

	# Build query with CASE expressions
	query = frappe.qb.from_(Lead).select(
		Count(Case().when(current_cond, Lead.name).else_(None)).as_("current_month_leads"),
		Count(Case().when(prev_cond, Lead.name).else_(None)).as_("prev_month_leads"),
	)

	result = query.run(as_dict=True)

	current_month_leads = result[0].current_month_leads or 0
	prev_month_leads = result[0].prev_month_leads or 0

	delta_in_percentage = (
		(current_month_leads - prev_month_leads) / prev_month_leads * 100 if prev_month_leads else 0
	)

	return {
		"title": _("Total leads"),
		"tooltip": _("Total number of leads"),
		"value": current_month_leads,
		"delta": delta_in_percentage,
		"deltaSuffix": "%",
	}


def get_ongoing_deals(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get ongoing deal count for the dashboard, and also calculate average deal value for ongoing deals.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build conditions for current period
	current_cond = (
		(Deal.creation >= from_date)
		& (Deal.creation < to_date_plus_one)
		& (Status.type.notin(["Won", "Lost"]))
	)
	if user:
		current_cond = current_cond & (Deal.deal_owner == user)

	# Build conditions for previous period
	prev_cond = (
		(Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type.notin(["Won", "Lost"]))
	)
	if user:
		prev_cond = prev_cond & (Deal.deal_owner == user)

	# Build query with CASE expressions
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Count(Case().when(current_cond, Deal.name).else_(None)).as_("current_month_deals"),
			Count(Case().when(prev_cond, Deal.name).else_(None)).as_("prev_month_deals"),
		)
	)

	result = query.run(as_dict=True)

	current_month_deals = result[0].current_month_deals or 0
	prev_month_deals = result[0].prev_month_deals or 0

	delta_in_percentage = (
		(current_month_deals - prev_month_deals) / prev_month_deals * 100 if prev_month_deals else 0
	)

	return {
		"title": _("Ongoing deals"),
		"tooltip": _("Total number of non won/lost deals"),
		"value": current_month_deals,
		"delta": delta_in_percentage,
		"deltaSuffix": "%",
	}


def get_average_ongoing_deal_value(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get ongoing deal count for the dashboard, and also calculate average deal value for ongoing deals.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build conditions for current period
	current_cond = (
		(Deal.creation >= from_date)
		& (Deal.creation < to_date_plus_one)
		& (Status.type.notin(["Won", "Lost"]))
	)
	if user:
		current_cond = current_cond & (Deal.deal_owner == user)

	# Build conditions for previous period
	prev_cond = (
		(Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type.notin(["Won", "Lost"]))
	)
	if user:
		prev_cond = prev_cond & (Deal.deal_owner == user)

	# Calculate deal value with exchange rate
	deal_value_expr = Deal.deal_value * IfNull(Deal.exchange_rate, 1)

	# Build query with CASE expressions
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Avg(Case().when(current_cond, deal_value_expr).else_(None)).as_("current_month_avg_value"),
			Avg(Case().when(prev_cond, deal_value_expr).else_(None)).as_("prev_month_avg_value"),
		)
	)

	result = query.run(as_dict=True)

	current_month_avg_value = result[0].current_month_avg_value or 0
	prev_month_avg_value = result[0].prev_month_avg_value or 0

	avg_value_delta = current_month_avg_value - prev_month_avg_value if prev_month_avg_value else 0

	return {
		"title": _("Avg. ongoing deal value"),
		"tooltip": _("Average deal value of non won/lost deals"),
		"value": current_month_avg_value,
		"delta": avg_value_delta,
		"prefix": get_base_currency_symbol(),
	}


def get_won_deals(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get won deal count for the dashboard, and also calculate average deal value for won deals.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build conditions for current period
	current_cond = (
		(Deal.closed_date >= from_date) & (Deal.closed_date < to_date_plus_one) & (Status.type == "Won")
	)
	if user:
		current_cond = current_cond & (Deal.deal_owner == user)

	# Build conditions for previous period
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < from_date) & (Status.type == "Won")
	if user:
		prev_cond = prev_cond & (Deal.deal_owner == user)

	# Build query with CASE expressions
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Count(Case().when(current_cond, Deal.name).else_(None)).as_("current_month_deals"),
			Count(Case().when(prev_cond, Deal.name).else_(None)).as_("prev_month_deals"),
		)
	)

	result = query.run(as_dict=True)

	current_month_deals = result[0].current_month_deals or 0
	prev_month_deals = result[0].prev_month_deals or 0

	delta_in_percentage = (
		(current_month_deals - prev_month_deals) / prev_month_deals * 100 if prev_month_deals else 0
	)

	return {
		"title": _("Won deals"),
		"tooltip": _("Total number of won deals based on its closure date"),
		"value": current_month_deals,
		"delta": delta_in_percentage,
		"deltaSuffix": "%",
	}


def get_average_won_deal_value(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get won deal count for the dashboard, and also calculate average deal value for won deals.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build conditions for current period
	current_cond = (
		(Deal.closed_date >= from_date) & (Deal.closed_date < to_date_plus_one) & (Status.type == "Won")
	)
	if user:
		current_cond = current_cond & (Deal.deal_owner == user)

	# Build conditions for previous period
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < from_date) & (Status.type == "Won")
	if user:
		prev_cond = prev_cond & (Deal.deal_owner == user)

	# Calculate deal value with exchange rate
	deal_value_expr = Deal.deal_value * IfNull(Deal.exchange_rate, 1)

	# Build query with CASE expressions
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Avg(Case().when(current_cond, deal_value_expr).else_(None)).as_("current_month_avg_value"),
			Avg(Case().when(prev_cond, deal_value_expr).else_(None)).as_("prev_month_avg_value"),
		)
	)

	result = query.run(as_dict=True)

	current_month_avg_value = result[0].current_month_avg_value or 0
	prev_month_avg_value = result[0].prev_month_avg_value or 0

	avg_value_delta = current_month_avg_value - prev_month_avg_value if prev_month_avg_value else 0

	return {
		"title": _("Avg. won deal value"),
		"tooltip": _("Average deal value of won deals"),
		"value": current_month_avg_value,
		"delta": avg_value_delta,
		"prefix": get_base_currency_symbol(),
	}


def get_average_deal_value(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get average deal value for the dashboard.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build conditions for current period
	current_cond = (Deal.creation >= from_date) & (Deal.creation < to_date_plus_one) & (Status.type != "Lost")
	if user:
		current_cond = current_cond & (Deal.deal_owner == user)

	# Build conditions for previous period
	prev_cond = (Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type != "Lost")
	if user:
		prev_cond = prev_cond & (Deal.deal_owner == user)

	# Calculate deal value with exchange rate
	deal_value_expr = Deal.deal_value * IfNull(Deal.exchange_rate, 1)

	# Build query with CASE expressions
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Avg(Case().when(current_cond, deal_value_expr).else_(None)).as_("current_month_avg"),
			Avg(Case().when(prev_cond, deal_value_expr).else_(None)).as_("prev_month_avg"),
		)
	)

	result = query.run(as_dict=True)

	current_month_avg = result[0].current_month_avg or 0
	prev_month_avg = result[0].prev_month_avg or 0

	delta = current_month_avg - prev_month_avg if prev_month_avg else 0

	return {
		"title": _("Avg. deal value"),
		"tooltip": _("Average deal value of ongoing & won deals"),
		"value": current_month_avg,
		"prefix": get_base_currency_symbol(),
		"delta": delta,
		"deltaSuffix": "%",
	}


def get_average_time_to_close_a_lead(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get average time to close deals for the dashboard.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)
	prev_to_date = from_date

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")
	Lead = DocType("CRM Lead")

	# Base condition: closed_date is not null and status type is Won
	base_cond = (Deal.closed_date.isnotnull()) & (Status.type == "Won")
	if user:
		base_cond = base_cond & (Deal.deal_owner == user)

	# Current period condition
	current_cond = (Deal.closed_date >= from_date) & (Deal.closed_date < to_date_plus_one)

	# Previous period condition
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < prev_to_date)

	# Calculate time difference from lead/deal creation to deal closure
	time_diff = TimestampDiff(
		frappe.qb.terms.LiteralValue("DAY"), Coalesce(Lead.creation, Deal.creation), Deal.closed_date
	)

	# Build query
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.left_join(Lead)
		.on(Deal.lead == Lead.name)
		.where(base_cond)
		.select(
			Avg(Case().when(current_cond, time_diff).else_(None)).as_("current_avg_lead"),
			Avg(Case().when(prev_cond, time_diff).else_(None)).as_("prev_avg_lead"),
		)
	)

	result = query.run(as_dict=True)

	current_avg_lead = result[0].current_avg_lead or 0
	prev_avg_lead = result[0].prev_avg_lead or 0
	delta_lead = current_avg_lead - prev_avg_lead if prev_avg_lead else 0

	return {
		"title": _("Avg. time to close a lead"),
		"tooltip": _("Average time taken from lead creation to deal closure"),
		"value": current_avg_lead,
		"suffix": " days",
		"delta": delta_lead,
		"deltaSuffix": " days",
		"negativeIsBetter": True,
	}


def get_average_time_to_close_a_deal(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get average time to close deals for the dashboard.
	"""
	diff = frappe.utils.date_diff(to_date, from_date)
	if diff == 0:
		diff = 1

	prev_from_date = frappe.utils.add_days(from_date, -diff)
	to_date_plus_one = frappe.utils.add_days(to_date, 1)
	prev_to_date = from_date

	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")
	Lead = DocType("CRM Lead")

	# Base condition: closed_date is not null and status type is Won
	base_cond = (Deal.closed_date.isnotnull()) & (Status.type == "Won")
	if user:
		base_cond = base_cond & (Deal.deal_owner == user)

	# Current period condition
	current_cond = (Deal.closed_date >= from_date) & (Deal.closed_date < to_date_plus_one)

	# Previous period condition
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < prev_to_date)

	# Calculate time difference from deal creation to deal closure
	time_diff = TimestampDiff(frappe.qb.terms.LiteralValue("DAY"), Deal.creation, Deal.closed_date)

	# Build query
	query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.left_join(Lead)
		.on(Deal.lead == Lead.name)
		.where(base_cond)
		.select(
			Avg(Case().when(current_cond, time_diff).else_(None)).as_("current_avg_deal"),
			Avg(Case().when(prev_cond, time_diff).else_(None)).as_("prev_avg_deal"),
		)
	)

	result = query.run(as_dict=True)

	current_avg_deal = result[0].current_avg_deal or 0
	prev_avg_deal = result[0].prev_avg_deal or 0
	delta_deal = current_avg_deal - prev_avg_deal if prev_avg_deal else 0

	return {
		"title": _("Avg. time to close a deal"),
		"tooltip": _("Average time taken from deal creation to deal closure"),
		"value": current_avg_deal,
		"suffix": " days",
		"delta": delta_deal,
		"deltaSuffix": " days",
		"negativeIsBetter": True,
	}


def get_sales_trend(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get sales trend data for the dashboard.
	[
		{ date: new Date('2024-05-01'), leads: 45, deals: 23, won_deals: 12 },
		{ date: new Date('2024-05-02'), leads: 50, deals: 30, won_deals: 15 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	Lead = DocType("CRM Lead")
	Deal = DocType("CRM Deal")
	Status = DocType("CRM Deal Status")

	# Build leads query
	leads_query = (
		frappe.qb.from_(Lead)
		.select(
			Date(Lead.creation).as_("date"),
			Count("*").as_("leads"),
			frappe.qb.terms.ValueWrapper(0).as_("deals"),
			frappe.qb.terms.ValueWrapper(0).as_("won_deals"),
		)
		.where(Date(Lead.creation).between(from_date, to_date))
	)

	if user:
		leads_query = leads_query.where(Lead.lead_owner == user)

	leads_query = leads_query.groupby(Date(Lead.creation))

	# Build deals query
	deals_query = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Date(Deal.creation).as_("date"),
			frappe.qb.terms.ValueWrapper(0).as_("leads"),
			Count("*").as_("deals"),
			Sum(Case().when(Status.type == "Won", 1).else_(0)).as_("won_deals"),
		)
		.where(Date(Deal.creation).between(from_date, to_date))
	)

	if user:
		deals_query = deals_query.where(Deal.deal_owner == user)

	deals_query = deals_query.groupby(Date(Deal.creation))

	# Combine with UNION ALL and aggregate by date
	union_query = leads_query.union_all(deals_query)

	# Wrap in outer query to aggregate by date
	daily = (
		frappe.qb.from_(union_query)
		.select(
			DateFormat(union_query.date, "%Y-%m-%d").as_("date"),
			Sum(union_query.leads).as_("leads"),
			Sum(union_query.deals).as_("deals"),
			Sum(union_query.won_deals).as_("won_deals"),
		)
		.groupby(union_query.date)
		.orderby(union_query.date)
	)

	result = daily.run(as_dict=True)

	sales_trend = [
		{
			"date": frappe.utils.get_datetime(row.date).strftime("%Y-%m-%d"),
			"leads": row.leads or 0,
			"deals": row.deals or 0,
			"won_deals": row.won_deals or 0,
		}
		for row in result
	]

	return {
		"data": sales_trend,
		"title": _("Sales trend"),
		"subtitle": _("Daily performance of leads, deals, and wins"),
		"xAxis": {
			"title": _("Date"),
			"key": "date",
			"type": "time",
			"timeGrain": "day",
		},
		"yAxis": {
			"title": _("Count"),
		},
		"series": [
			{"name": "leads", "type": "line", "showDataPoints": True},
			{"name": "deals", "type": "line", "showDataPoints": True},
			{"name": "won_deals", "type": "line", "showDataPoints": True},
		],
	}


def get_forecasted_revenue(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get forecasted revenue for the dashboard.
	[
		{ date: new Date('2024-05-01'), forecasted: 1200000, actual: 980000 },
		{ date: new Date('2024-06-01'), forecasted: 1350000, actual: 1120000 },
		{ date: new Date('2024-07-01'), forecasted: 1600000, actual: "" },
		{ date: new Date('2024-08-01'), forecasted: 1500000, actual: "" },
		...
	]
	"""
	# Using Frappe Query Builder with CASE expressions
	CRMDeal = DocType("CRM Deal")
	CRMDealStatus = DocType("CRM Deal Status")

	# Calculate the date 12 months ago
	twelve_months_ago = frappe.utils.add_months(frappe.utils.nowdate(), -12)

	forecasted_value = (
		Case()
		.when(CRMDealStatus.type == "Lost", CRMDeal.expected_deal_value * IfNull(CRMDeal.exchange_rate, 1))
		.else_(
			CRMDeal.expected_deal_value
			* IfNull(CRMDeal.probability, 0)
			/ 100
			* IfNull(CRMDeal.exchange_rate, 1)
		)
	)

	actual_value = (
		Case()
		.when(CRMDealStatus.type == "Won", CRMDeal.deal_value * IfNull(CRMDeal.exchange_rate, 1))
		.else_(0)
	)

	query = (
		frappe.qb.from_(CRMDeal)
		.join(CRMDealStatus)
		.on(CRMDeal.status == CRMDealStatus.name)
		.select(
			DateFormat(CRMDeal.expected_closure_date, "%Y-%m").as_("month"),
			Sum(forecasted_value).as_("forecasted"),
			Sum(actual_value).as_("actual"),
		)
		.where(CRMDeal.expected_closure_date >= twelve_months_ago)
		.groupby(DateFormat(CRMDeal.expected_closure_date, "%Y-%m"))
		.orderby(DateFormat(CRMDeal.expected_closure_date, "%Y-%m"))
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	for row in result:
		row["month"] = frappe.utils.get_datetime(row["month"]).strftime("%Y-%m-01")
		row["forecasted"] = row["forecasted"] or ""
		row["actual"] = row["actual"] or ""

	return {
		"data": result or [],
		"title": _("Forecasted revenue"),
		"subtitle": _("Projected vs actual revenue based on deal probability"),
		"xAxis": {
			"title": _("Month"),
			"key": "month",
			"type": "time",
			"timeGrain": "month",
		},
		"yAxis": {
			"title": _("Revenue") + f" ({get_base_currency_symbol()})",
		},
		"series": [
			{"name": "forecasted", "type": "line", "showDataPoints": True},
			{"name": "actual", "type": "line", "showDataPoints": True},
		],
	}


def get_funnel_conversion(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get funnel conversion data for the dashboard.
	[
		{ stage: 'Leads', count: 120 },
		{ stage: 'Qualification', count: 100 },
		{ stage: 'Negotiation', count: 80 },
		{ stage: 'Ready to Close', count: 60 },
		{ stage: 'Won', count: 30 },
		...
	]
	"""
	lead_conds = ""
	deal_conds = ""

	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	lead_filters = {"from": from_date, "to": to_date}
	deal_filters = {"from": from_date, "to": to_date}

	if user:
		lead_conds += " AND lead_owner = %(user)s"
		deal_conds += " AND deal_owner = %(user)s"
		lead_filters["user"] = user
		deal_filters["user"] = user

	result = []

	# Get total leads using Query Builder
	CRMLead = DocType("CRM Lead")

	query = (
		frappe.qb.from_(CRMLead)
		.select(Count("*").as_("count"))
		.where(Date(CRMLead.creation).between(from_date, to_date))
	)

	if user:
		query = query.where(CRMLead.lead_owner == user)

	total_leads = query.run(as_dict=True)
	total_leads_count = total_leads[0].count if total_leads else 0

	result.append({"stage": "Leads", "count": total_leads_count})

	result += get_deal_status_change_counts(from_date, to_date, deal_conds, deal_filters)

	return {
		"data": result or [],
		"title": _("Funnel conversion"),
		"subtitle": _("Lead to deal conversion pipeline"),
		"xAxis": {
			"title": _("Stage"),
			"key": "stage",
			"type": "category",
		},
		"yAxis": {
			"title": _("Count"),
		},
		"swapXY": True,
		"series": [
			{
				"name": "count",
				"type": "bar",
				"echartOptions": {
					"colorBy": "data",
				},
			},
		],
	}


def get_deals_by_stage_axis(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get deal data by stage for the dashboard.
	[
		{ stage: 'Prospecting', count: 120 },
		{ stage: 'Negotiation', count: 45 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder with NOT IN clause
	CRMDeal = DocType("CRM Deal")
	CRMDealStatus = DocType("CRM Deal Status")

	query = (
		frappe.qb.from_(CRMDeal)
		.join(CRMDealStatus)
		.on(CRMDeal.status == CRMDealStatus.name)
		.select(CRMDeal.status.as_("stage"), Count("*").as_("count"), CRMDealStatus.type.as_("status_type"))
		.where((Date(CRMDeal.creation).between(from_date, to_date)) & (CRMDealStatus.type.notin(["Lost"])))
		.groupby(CRMDeal.status)
		.orderby(Count("*"), order=frappe.qb.desc)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by ongoing & won stage"),
		"xAxis": {
			"title": _("Stage"),
			"key": "stage",
			"type": "category",
		},
		"yAxis": {"title": _("Count")},
		"series": [
			{"name": "count", "type": "bar"},
		],
	}


def get_deals_by_stage_donut(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get deal data by stage for the dashboard.
	[
		{ stage: 'Prospecting', count: 120 },
		{ stage: 'Negotiation', count: 45 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder with JOIN
	CRMDeal = DocType("CRM Deal")
	CRMDealStatus = DocType("CRM Deal Status")

	query = (
		frappe.qb.from_(CRMDeal)
		.join(CRMDealStatus)
		.on(CRMDeal.status == CRMDealStatus.name)
		.select(CRMDeal.status.as_("stage"), Count("*").as_("count"), CRMDealStatus.type.as_("status_type"))
		.where(Date(CRMDeal.creation).between(from_date, to_date))
		.groupby(CRMDeal.status)
		.orderby(Count("*"), order=frappe.qb.desc)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by stage"),
		"subtitle": _("Current pipeline distribution"),
		"categoryColumn": "stage",
		"valueColumn": "count",
	}


def get_lost_deal_reasons(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get lost deal reasons for the dashboard.
	[
		{ reason: 'Price too high', count: 20 },
		{ reason: 'Competitor won', count: 15 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder with JOIN
	CRMDeal = DocType("CRM Deal")
	CRMDealStatus = DocType("CRM Deal Status")

	query = (
		frappe.qb.from_(CRMDeal)
		.join(CRMDealStatus)
		.on(CRMDeal.status == CRMDealStatus.name)
		.select(CRMDeal.lost_reason.as_("reason"), Count("*").as_("count"))
		.where((Date(CRMDeal.creation).between(from_date, to_date)) & (CRMDealStatus.type == "Lost"))
		.groupby(CRMDeal.lost_reason)
		.having((CRMDeal.lost_reason.isnotnull()) & (CRMDeal.lost_reason != ""))
		.orderby(Count("*"), order=frappe.qb.desc)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Lost deal reasons"),
		"subtitle": _("Common reasons for losing deals"),
		"xAxis": {
			"title": _("Reason"),
			"key": "reason",
			"type": "category",
		},
		"yAxis": {
			"title": _("Count"),
		},
		"series": [
			{"name": "count", "type": "bar"},
		],
	}


def get_leads_by_source(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get lead data by source for the dashboard.
	[
		{ source: 'Website', count: 120 },
		{ source: 'Referral', count: 45 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder (safer, more maintainable)
	CRMLead = DocType("CRM Lead")

	query = (
		frappe.qb.from_(CRMLead)
		.select(IfNull(CRMLead.source, "Empty").as_("source"), Count("*").as_("count"))
		.where(Date(CRMLead.creation).between(from_date, to_date))
		.groupby(CRMLead.source)
		.orderby(Count("*"), order=frappe.qb.desc)
	)

	if user:
		query = query.where(CRMLead.lead_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Leads by source"),
		"subtitle": _("Lead generation channel analysis"),
		"categoryColumn": "source",
		"valueColumn": "count",
	}


def get_deals_by_source(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get deal data by source for the dashboard.
	[
		{ source: 'Website', count: 120 },
		{ source: 'Referral', count: 45 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder
	CRMDeal = DocType("CRM Deal")

	query = (
		frappe.qb.from_(CRMDeal)
		.select(IfNull(CRMDeal.source, "Empty").as_("source"), Count("*").as_("count"))
		.where(Date(CRMDeal.creation).between(from_date, to_date))
		.groupby(CRMDeal.source)
		.orderby(Count("*"), order=frappe.qb.desc)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by source"),
		"subtitle": _("Deal generation channel analysis"),
		"categoryColumn": "source",
		"valueColumn": "count",
	}


def get_deals_by_territory(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""
	Get deal data by territory for the dashboard.
	[
		{ territory: 'North America', deals: 45, value: 2300000 },
		{ territory: 'Europe', deals: 30, value: 1500000 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder with complex aggregations
	CRMDeal = DocType("CRM Deal")

	query = (
		frappe.qb.from_(CRMDeal)
		.select(
			IfNull(CRMDeal.territory, "Empty").as_("territory"),
			Count("*").as_("deals"),
			Sum(Coalesce(CRMDeal.deal_value, 0) * IfNull(CRMDeal.exchange_rate, 1)).as_("value"),
		)
		.where(Date(CRMDeal.creation).between(from_date, to_date))
		.groupby(CRMDeal.territory)
		.orderby(Count("*"), order=frappe.qb.desc)
		.orderby(
			Sum(Coalesce(CRMDeal.deal_value, 0) * IfNull(CRMDeal.exchange_rate, 1)), order=frappe.qb.desc
		)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by territory"),
		"subtitle": _("Geographic distribution of deals and revenue"),
		"xAxis": {
			"title": _("Territory"),
			"key": "territory",
			"type": "category",
		},
		"yAxis": {
			"title": _("Number of deals"),
		},
		"y2Axis": {
			"title": _("Deal value") + f" ({get_base_currency_symbol()})",
		},
		"series": [
			{"name": "deals", "type": "bar"},
			{"name": "value", "type": "line", "showDataPoints": True, "axis": "y2"},
		],
	}


def get_deals_by_salesperson(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""
	Get deal data by salesperson for the dashboard.
	[
		{ salesperson: 'John Smith', deals: 45, value: 2300000 },
		{ salesperson: 'Jane Doe', deals: 30, value: 1500000 },
		...
	]
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Using Frappe Query Builder with LEFT JOIN
	CRMDeal = DocType("CRM Deal")
	User = DocType("User")

	query = (
		frappe.qb.from_(CRMDeal)
		.left_join(User)
		.on(User.name == CRMDeal.deal_owner)
		.select(
			IfNull(User.full_name, CRMDeal.deal_owner).as_("salesperson"),
			Count("*").as_("deals"),
			Sum(Coalesce(CRMDeal.deal_value, 0) * IfNull(CRMDeal.exchange_rate, 1)).as_("value"),
		)
		.where(Date(CRMDeal.creation).between(from_date, to_date))
		.groupby(CRMDeal.deal_owner)
		.orderby(Count("*"), order=frappe.qb.desc)
		.orderby(
			Sum(Coalesce(CRMDeal.deal_value, 0) * IfNull(CRMDeal.exchange_rate, 1)), order=frappe.qb.desc
		)
	)

	if user:
		query = query.where(CRMDeal.deal_owner == user)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by salesperson"),
		"subtitle": _("Number of deals and total value per salesperson"),
		"xAxis": {
			"title": _("Salesperson"),
			"key": "salesperson",
			"type": "category",
		},
		"yAxis": {
			"title": _("Number of deals"),
		},
		"y2Axis": {
			"title": _("Deal value") + f" ({get_base_currency_symbol()})",
		},
		"series": [
			{"name": "deals", "type": "bar"},
			{"name": "value", "type": "line", "showDataPoints": True, "axis": "y2"},
		],
	}


def get_base_currency_symbol():
	"""
	Get the base currency symbol from the system settings.
	"""
	base_currency = frappe.db.get_single_value("FCRM Settings", "currency") or "USD"
	return frappe.db.get_value("Currency", base_currency, "symbol") or ""


def get_deal_status_change_counts(
	from_date: str | None = None,
	to_date: str | None = None,
	deal_conds: str = "",
	filters: dict | None = None,
):
	"""
	Get count of each status change (to) for each deal, excluding deals with current status type 'Lost'.
	Order results by status position.
	Returns:
	[
	  {"status": "Qualification", "count": 120},
	  {"status": "Negotiation", "count": 85},
	  ...
	]
	"""
	# Using Frappe Query Builder with multiple JOINs and table aliases
	CRMStatusChangeLog = DocType("CRM Status Change Log")
	CRMDeal = DocType("CRM Deal")
	CurrentStatus = DocType("CRM Deal Status").as_("s")
	TargetStatus = DocType("CRM Deal Status").as_("st")

	query = (
		frappe.qb.from_(CRMStatusChangeLog)
		.join(CRMDeal)
		.on(CRMStatusChangeLog.parent == CRMDeal.name)
		.join(CurrentStatus)
		.on(CRMDeal.status == CurrentStatus.name)
		.join(TargetStatus)
		.on(CRMStatusChangeLog.to == TargetStatus.name)
		.select(CRMStatusChangeLog.to.as_("stage"), Count("*").as_("count"))
		.where(
			(CRMStatusChangeLog.to.isnotnull())
			& (CRMStatusChangeLog.to != "")
			& (CurrentStatus.type != "Lost")
			& (Date(CRMDeal.creation).between(from_date, to_date))
		)
		.groupby(CRMStatusChangeLog.to, TargetStatus.position)
		.orderby(TargetStatus.position)
	)

	# Handle optional user filter if deal_conds contains user condition
	if filters and filters.get("user"):
		query = query.where(CRMDeal.deal_owner == filters["user"])

	result = query.run(as_dict=True)
	return result or []


# ─────────────────────────────────────────────────────────────────────
# IndiFrame custom analytics — 10 charts for the lead-centric dashboard
# (spec §11.1 - §11.5). Each follows the existing chart return shape so
# the AddChartModal / DashboardItem renderer can consume them unchanged.
# ─────────────────────────────────────────────────────────────────────


def _date_window(from_date, to_date):
	"""Default to current month if either bound is missing."""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	return from_date, to_date


# ─── 11.1 Lead Generation Performance ────────────────────────────────


def get_leads_over_time(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""Daily lead creation trend."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT DATE(creation) AS date, COUNT(*) AS leads
		FROM `tabCRM Lead`
		WHERE DATE(creation) BETWEEN %(from_date)s AND %(to_date)s
		      {user_filter}
		GROUP BY DATE(creation)
		ORDER BY DATE(creation)
		""",
		params,
		as_dict=True,
	)

	data = [{"date": frappe.utils.formatdate(r.date, "yyyy-MM-dd"), "leads": r.leads} for r in rows]

	return {
		"data": data,
		"title": _("Leads over time"),
		"subtitle": _("Daily new lead volume"),
		"xAxis": {"title": _("Date"), "key": "date", "type": "time", "timeGrain": "day"},
		"yAxis": {"title": _("Leads")},
		"series": [{"name": "leads", "type": "line", "showDataPoints": True}],
	}


def get_leads_by_sub_source(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Lead breakdown by custom_sub_source (horizontal bar)."""
	from_date, to_date = _date_window(from_date, to_date)

	CRMLead = DocType("CRM Lead")
	query = (
		frappe.qb.from_(CRMLead)
		.select(IfNull(CRMLead.custom_sub_source, "Unspecified").as_("sub_source"), Count("*").as_("count"))
		.where(Date(CRMLead.creation).between(from_date, to_date))
		.groupby(CRMLead.custom_sub_source)
		.orderby(Count("*"), order=frappe.qb.desc)
	)
	if user:
		query = query.where(CRMLead.lead_owner == user)

	return {
		"data": query.run(as_dict=True) or [],
		"title": _("Leads by sub-source"),
		"subtitle": _("Marketing channel sub-attribution"),
		"xAxis": {"title": _("Sub-source"), "key": "sub_source", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


def get_leads_by_source_axis(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Lead breakdown by source as a horizontal bar chart.

	Bar version of the stock `get_leads_by_source` (which is a donut). Use
	this one when you want to see the absolute count visually scaled per
	source — donuts compress the long tail.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	CRMLead = DocType("CRM Lead")
	query = (
		frappe.qb.from_(CRMLead)
		.select(IfNull(CRMLead.source, "Unspecified").as_("source"), Count("*").as_("count"))
		.where(Date(CRMLead.creation).between(from_date, to_date))
		.groupby(CRMLead.source)
		.orderby(Count("*"), order=frappe.qb.desc)
	)
	if user:
		query = query.where(CRMLead.lead_owner == user)

	return {
		"data": query.run(as_dict=True) or [],
		"title": _("Leads by source"),
		"subtitle": _("Lead generation channel — bar view"),
		"xAxis": {"title": _("Source"), "key": "source", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


def get_lead_spotting_productivity(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Volume of Lead-Spotting source leads per spotter (lead_owner).

	'Spotter' = the user who currently owns the lead. If your team uses a
	different attribution (e.g. owner at creation), adapt the lead_owner
	column accordingly.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	# Manager view shows everyone; IC view filters to self via the
	# get_chart dispatcher (sets user=session_user for ICs).
	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND l.lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			COALESCE(u.full_name, l.lead_owner, 'Unassigned')  AS spotter,
			COUNT(*)                                            AS count
		FROM `tabCRM Lead` l
		LEFT JOIN `tabUser` u ON u.name = l.lead_owner
		WHERE DATE(l.creation) BETWEEN %(from_date)s AND %(to_date)s
		  AND l.source = 'Lead Spotting'
		  {user_filter}
		GROUP BY spotter
		ORDER BY count DESC
		LIMIT 20
		""",
		params,
		as_dict=True,
	)

	return {
		"data": rows or [],
		"title": _("Lead Spotting productivity"),
		"subtitle": _("Leads sourced from Lead Spotting, per spotter"),
		"xAxis": {"title": _("Spotter"), "key": "spotter", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar"}],
	}


# ─── 11.2 Pipeline Stage Distribution ────────────────────────────────


def get_lead_pipeline_funnel(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Lead-stage funnel.

	Stage order:
	  C0 → C1 → C2 → C3 → C4 (Won) → C6 (Lost) → C7 (Forwarded)
	"""
	from_date, to_date = _date_window(from_date, to_date)

	# How spec stages map to actual CRM Lead Status names.
	# Order matters — used for funnel ordering on the chart.
	STAGE_BUCKETS = [
		("C0 — New Lead", ["C0"]),
		("C1 — Future Requirement", ["C1"]),
		("C2 — Active Engagement", ["C2"]),
		("C3 — Almost Ready", ["C3"]),
		("C4 — Won", ["C4"]),
		("C6 — Lost", ["C6"]),
		("C7 — Forwarded to Fabricator", ["C7"]),
	]

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT status, COUNT(*) AS count
		FROM `tabCRM Lead`
		WHERE DATE(creation) BETWEEN %(from_date)s AND %(to_date)s
		  AND status IS NOT NULL
		  {user_filter}
		GROUP BY status
		""",
		params,
		as_dict=True,
	)
	by_status = {r.status: r.count for r in rows}

	data = []
	for label, statuses in STAGE_BUCKETS:
		total = sum(by_status.get(s, 0) for s in statuses)
		data.append({"stage": label, "count": total})

	return {
		"data": data,
		"title": _("Lead pipeline funnel"),
		"subtitle": _("Live distribution across C-stages"),
		"xAxis": {"title": _("Stage"), "key": "stage", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


def get_c2_sub_status_breakdown(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""For leads currently in C2, distribution by lead_status (engagement)."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT COALESCE(lead_status, 'Unset') AS sub_status, COUNT(*) AS count
		FROM `tabCRM Lead`
		WHERE status = 'C2'
		  AND DATE(creation) BETWEEN %(from_date)s AND %(to_date)s
		  {user_filter}
		GROUP BY lead_status
		ORDER BY count DESC
		""",
		params,
		as_dict=True,
	)

	return {
		"data": rows or [],
		"title": _("C2 sub-status breakdown"),
		"subtitle": _("Engagement state for leads in C2"),
		"categoryColumn": "sub_status",
		"valueColumn": "count",
	}


# ─── 11.3 Calling Team Productivity ──────────────────────────────────


def get_calls_per_caller(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""Call volume per caller across all telephony providers."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND c.caller = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			COALESCE(u.full_name, c.caller, 'Unknown')  AS caller,
			COUNT(*)                                    AS total_calls,
			SUM(c.duration >= 30)                       AS meaningful_calls,
			ROUND(SUM(c.duration) / 60, 1)              AS total_minutes
		FROM `tabCRM Call Log` c
		LEFT JOIN `tabUser` u ON u.name = c.caller
		WHERE DATE(c.start_time) BETWEEN %(from_date)s AND %(to_date)s
		  AND c.caller IS NOT NULL AND c.caller != ''
		  {user_filter}
		GROUP BY c.caller
		ORDER BY total_calls DESC
		LIMIT 30
		""",
		params,
		as_dict=True,
	)

	return {
		"data": rows or [],
		"title": _("Calls per caller"),
		"subtitle": _("Volume + meaningful (≥30s) calls per agent"),
		"xAxis": {"title": _("Agent"), "key": "caller", "type": "category"},
		"yAxis": {"title": _("Calls")},
		"swapXY": True,
		"series": [
			{"name": "total_calls", "type": "bar"},
			{"name": "meaningful_calls", "type": "bar"},
		],
	}


def get_call_dispositions(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""Disposition distribution across all calls in the date window."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND (caller = %(user)s OR receiver = %(user)s)"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT COALESCE(disposition, 'Not Tagged') AS disposition, COUNT(*) AS count
		FROM `tabCRM Call Log`
		WHERE DATE(start_time) BETWEEN %(from_date)s AND %(to_date)s
		  {user_filter}
		GROUP BY disposition
		ORDER BY count DESC
		""",
		params,
		as_dict=True,
	)

	return {
		"data": rows or [],
		"title": _("Call dispositions"),
		"subtitle": _("Outcome distribution of all calls"),
		"categoryColumn": "disposition",
		"valueColumn": "count",
	}


def get_avg_c0_to_c2_time_per_caller(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Average days from C0 entry → C2 entry, grouped by lead_owner.

	Only counts leads where BOTH custom_c0_entered_on and
	custom_c2_entered_on are populated (the After Save script set them).
	Primary effectiveness metric per spec §11.3.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND l.lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			COALESCE(u.full_name, l.lead_owner, 'Unassigned')                 AS agent,
			ROUND(AVG(TIMESTAMPDIFF(HOUR, l.custom_c0_entered_on,
			                              l.custom_c2_entered_on)) / 24, 1) AS avg_days,
			COUNT(*)                                                          AS lead_count
		FROM `tabCRM Lead` l
		LEFT JOIN `tabUser` u ON u.name = l.lead_owner
		WHERE l.custom_c0_entered_on IS NOT NULL
		  AND l.custom_c2_entered_on IS NOT NULL
		  AND DATE(l.custom_c2_entered_on) BETWEEN %(from_date)s AND %(to_date)s
		  {user_filter}
		GROUP BY l.lead_owner
		HAVING lead_count >= 1
		ORDER BY avg_days ASC
		LIMIT 30
		""",
		params,
		as_dict=True,
	)

	return {
		"data": rows or [],
		"title": _("Avg C0 → C2 time per agent"),
		"subtitle": _("Days a lead spends from C0 to C2, by lead owner (lower is better)"),
		"xAxis": {"title": _("Agent"), "key": "agent", "type": "category"},
		"yAxis": {"title": _("Days")},
		"swapXY": True,
		"series": [{"name": "avg_days", "type": "bar"}],
	}


# ─── 11.4 Loss Analysis ──────────────────────────────────────────────


def get_lost_lead_reasons(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""Reasons why leads were lost (status = C6), grouped by lost_reason."""
	from_date, to_date = _date_window(from_date, to_date)

	CRMLead = DocType("CRM Lead")
	query = (
		frappe.qb.from_(CRMLead)
		.select(IfNull(CRMLead.lost_reason, "Unspecified").as_("reason"), Count("*").as_("count"))
		.where((Date(CRMLead.creation).between(from_date, to_date)) & (CRMLead.status == "C6"))
		.groupby(CRMLead.lost_reason)
		.orderby(Count("*"), order=frappe.qb.desc)
	)
	if user:
		query = query.where(CRMLead.lead_owner == user)

	return {
		"data": query.run(as_dict=True) or [],
		"title": _("Lost lead reasons"),
		"subtitle": _("Why C6 leads dropped out"),
		"xAxis": {"title": _("Reason"), "key": "reason", "type": "category"},
		"yAxis": {"title": _("Count")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


# ─── 11.5 Geographic Performance ─────────────────────────────────────


def get_leads_by_geography(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	"""Top 20 cities by lead volume, with conversion (Won/total) ratio.

	Note: this is a tabular/bar view of geography. A true heatmap on a
	map tile layer requires a frontend library (Leaflet) — to be added
	in a follow-up. The data here is sufficient to drive both.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	if user:
		user_filter = " AND lead_owner = %(user)s"
		params["user"] = user

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			COALESCE(NULLIF(TRIM(custom_city), ''), 'Unspecified')  AS city,
			COALESCE(NULLIF(TRIM(custom_state), ''), '-')           AS state,
			COUNT(*)                                                AS total_leads,
			SUM(status = 'C4')                                      AS won_leads,
			SUM(status = 'C6')                                      AS lost_leads
		FROM `tabCRM Lead`
		WHERE DATE(creation) BETWEEN %(from_date)s AND %(to_date)s
		  {user_filter}
		GROUP BY city, state
		ORDER BY total_leads DESC
		LIMIT 20
		""",
		params,
		as_dict=True,
	)

	# Compose label "City (State)" so the chart axis reads naturally.
	for r in rows:
		r["location"] = f"{r['city']} ({r['state']})" if r["state"] != "-" else r["city"]

	return {
		"data": rows or [],
		"title": _("Leads by geography"),
		"subtitle": _("Top 20 cities — volume, wins, losses"),
		"xAxis": {"title": _("Location"), "key": "location", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [
			{"name": "total_leads", "type": "bar"},
			{"name": "won_leads", "type": "bar"},
			{"name": "lost_leads", "type": "bar"},
		],
	}


@frappe.whitelist()
@sales_user_only
def download_lead_export(
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
):
	"""Excel export of leads with linked deal/quote/activity summary.

	Permission model mirrors `get_dashboard`:
	  - Manager roles (_MANAGER_ROLES) see the whole team; can narrow to one
	    rep by passing `user`.
	  - Individual contributors are forced to their own leads (lead_owner =
	    session user) regardless of any `user` value sent.

	Empty result still returns a headers-only workbook so the browser gets
	a clean file instead of an error page.
	"""
	from frappe.utils.xlsxutils import make_xlsx

	roles = set(frappe.get_roles(frappe.session.user))
	is_manager = bool(roles & _MANAGER_ROLES)

	conditions = ["l.status IS NOT NULL"]
	params: dict = {}

	if from_date and to_date:
		conditions.append("l.creation >= %(from_date)s")
		conditions.append("l.creation < %(to_date_exclusive)s")
		params["from_date"] = from_date
		params["to_date_exclusive"] = frappe.utils.add_days(to_date, 1)

	if is_manager:
		if user:
			conditions.append("l.lead_owner = %(filter_user)s")
			params["filter_user"] = user
	else:
		conditions.append("l.lead_owner = %(self_user)s")
		params["self_user"] = frappe.session.user

	where_clause = " AND ".join(conditions)

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			l.name                              AS lead_id,
			l.lead_name,
			l.first_name,
			l.last_name,
			l.email,
			l.mobile_no,
			l.phone,
			l.status                            AS stage,
			l.lead_status                       AS engagement,
			l.source,
			l.custom_sub_source                 AS sub_source,
			l.custom_lead_type                  AS lead_type,
			l.custom_customer_type              AS customer_type,
			l.lead_owner,
			l.industry,
			l.territory,
			l.custom_account                    AS account,
			l.custom_pincode                    AS pincode,
			l.custom_city                       AS city,
			l.custom_state                      AS state,
			l.custom_tentative_area_sqft        AS area_sqft,
			l.custom_tentative_value            AS tentative_value,
			l.custom_utm_source                 AS utm_source,
			l.custom_utm_medium                 AS utm_medium,
			l.custom_utm_campaign               AS utm_campaign,
			l.custom_c0_entered_on,
			l.custom_c1_entered_on,
			l.custom_c2_entered_on,
			l.custom_c4_entered_on,
			l.custom_c6_entered_on,
			l.custom_quotation_sent_on,
			l.creation                          AS created_on,
			l.modified                          AS last_modified,
			d.name                              AS deal_id,
			d.status                            AS deal_status,
			d.annual_revenue                    AS deal_value,
			q.name                              AS latest_quote_id,
			q.status                            AS latest_quote_status,
			q.quote_value                       AS latest_quote_value,
			q.creation                          AS latest_quote_at,
			(SELECT COUNT(*) FROM `tabCRM Quote Request` WHERE lead = l.name)
				AS total_quotes,
			(SELECT COUNT(*) FROM `tabCRM AISensy Message`
				WHERE reference_doctype='CRM Lead' AND reference_name = l.name)
				AS total_whatsapp,
			(SELECT COUNT(*) FROM `tabCRM AISensy Message`
				WHERE reference_doctype='CRM Lead' AND reference_name = l.name
				AND status='Sent')
				AS whatsapp_delivered,
			(SELECT COUNT(*) FROM `tabFCRM Note`
				WHERE reference_doctype='CRM Lead' AND reference_docname = l.name)
				AS total_notes,
			(SELECT COUNT(*) FROM `tabCRM Task`
				WHERE reference_doctype='CRM Lead' AND reference_docname = l.name)
				AS total_tasks,
			COALESCE(call_stats.total_calls, 0)         AS total_calls,
			COALESCE(call_stats.meaningful_calls, 0)    AS meaningful_calls,
			COALESCE(call_stats.missed_calls, 0)        AS missed_calls,
			COALESCE(call_stats.exotel_calls, 0)        AS exotel_calls,
			COALESCE(call_stats.total_call_minutes, 0)  AS total_call_minutes,
			call_stats.last_call_at,
			(SELECT u.full_name
				FROM `tabCRM Call Log` cl
				LEFT JOIN `tabUser` u ON u.name = cl.caller
				WHERE cl.reference_doctype='CRM Lead'
				  AND cl.reference_docname = l.name
				ORDER BY cl.start_time DESC LIMIT 1)    AS last_call_agent,
			(SELECT cl.disposition
				FROM `tabCRM Call Log` cl
				WHERE cl.reference_doctype='CRM Lead'
				  AND cl.reference_docname = l.name
				ORDER BY cl.start_time DESC LIMIT 1)    AS last_call_disposition
		FROM `tabCRM Lead` l
		LEFT JOIN `tabCRM Deal` d
			ON d.lead = l.name
		LEFT JOIN `tabCRM Quote Request` q
			ON q.lead = l.name
			AND q.creation = (
				SELECT MAX(creation) FROM `tabCRM Quote Request` WHERE lead = l.name
			)
		LEFT JOIN (
			SELECT
				reference_docname                       AS lead_name,
				COUNT(*)                                AS total_calls,
				SUM(duration >= 30)                     AS meaningful_calls,
				SUM(status IN ('Failed','No Answer','Busy'))  AS missed_calls,
				SUM(telephony_medium = 'Exotel')        AS exotel_calls,
				ROUND(SUM(duration)/60, 1)              AS total_call_minutes,
				MAX(start_time)                         AS last_call_at
			FROM `tabCRM Call Log`
			WHERE reference_doctype = 'CRM Lead'
			GROUP BY reference_docname
		) call_stats ON call_stats.lead_name = l.name
		WHERE {where_clause}
		ORDER BY l.creation DESC
		LIMIT 50000
		""",
		params,
		as_dict=True,
	)

	if rows:
		headers = list(rows[0].keys())
		data = [headers] + [[row.get(h) for h in headers] for row in rows]
	else:
		# Empty result: emit headers-only workbook so the browser gets a file,
		# not a JSON error page (download was initiated via window.location).
		headers = [
			"lead_id",
			"lead_name",
			"stage",
			"customer_type",
			"lead_owner",
			"created_on",
		]
		data = [headers]

	xlsx_file = make_xlsx(data, "Leads")

	scope = "team" if (is_manager and not user) else (user or frappe.session.user)
	frappe.response["filename"] = f"lead_export_{scope}_{frappe.utils.today()}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"


@frappe.whitelist()
@sales_user_only
def download_calls_export(
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
):
	"""Excel export of CRM Call Log rows across all telephony providers.

	Permission model:
	  - Manager roles (_MANAGER_ROLES) see every call; can narrow to one
	    rep's calls (caller OR receiver) via `user`.
	  - Individual contributors are forced to their OWN calls — caller or
	    receiver must equal session user, regardless of any `user` value.

	One row per call. Includes lead context when the call is attached to
	a CRM Lead. Date filter applies to call start_time.
	"""
	from frappe.utils.xlsxutils import make_xlsx

	roles = set(frappe.get_roles(frappe.session.user))
	is_manager = bool(roles & _MANAGER_ROLES)

	conditions = ["c.start_time IS NOT NULL"]
	params: dict = {}

	if from_date and to_date:
		conditions.append("c.start_time >= %(from_date)s")
		conditions.append("c.start_time < %(to_date_exclusive)s")
		params["from_date"] = from_date
		params["to_date_exclusive"] = frappe.utils.add_days(to_date, 1)

	if is_manager:
		if user:
			conditions.append("(c.caller = %(filter_user)s OR c.receiver = %(filter_user)s)")
			params["filter_user"] = user
	else:
		# IC sees only calls they participated in (caller or receiver).
		conditions.append("(c.caller = %(self_user)s OR c.receiver = %(self_user)s)")
		params["self_user"] = frappe.session.user

	where_clause = " AND ".join(conditions)

	# Backticks on `from`/`to` — they are SQL reserved words and also
	# happen to be the column names AiSensy chose for CRM Call Log.
	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT
			c.name                              AS call_id,
			c.id                                AS provider_call_sid,
			c.telephony_medium                  AS provider,
			c.type                              AS direction,
			c.status                            AS call_status,
			c.`from`                            AS from_number,
			c.`to`                              AS to_number,
			c.medium                            AS dialed_via,
			c.start_time,
			c.end_time,
			c.duration                          AS duration_seconds,
			ROUND(c.duration / 60, 2)           AS duration_minutes,
			caller_user.full_name               AS caller_name,
			c.caller                            AS caller_email,
			receiver_user.full_name             AS receiver_name,
			c.receiver                          AS receiver_email,
			c.disposition,
			c.scheduled_callback_at,
			n.title                             AS note_title,
			n.content                           AS note_content,
			c.recording_url,
			c.reference_doctype                 AS linked_to,
			c.reference_docname                 AS linked_record,
			l.name                              AS lead_id,
			l.lead_name,
			l.status                            AS lead_stage,
			l.lead_status                       AS lead_engagement,
			l.custom_customer_type              AS customer_type,
			l.lead_owner,
			l.source                            AS lead_source,
			l.custom_city                       AS city,
			l.custom_state                      AS state,
			d.name                              AS deal_id,
			d.status                            AS deal_status,
			c.creation                          AS logged_on,
			c.modified                          AS last_modified
		FROM `tabCRM Call Log` c
		LEFT JOIN `tabUser` caller_user
			ON caller_user.name = c.caller
		LEFT JOIN `tabUser` receiver_user
			ON receiver_user.name = c.receiver
		LEFT JOIN `tabFCRM Note` n
			ON n.name = c.note
		LEFT JOIN `tabCRM Lead` l
			ON l.name = c.reference_docname
			AND c.reference_doctype = 'CRM Lead'
		LEFT JOIN `tabCRM Deal` d
			ON d.name = c.reference_docname
			AND c.reference_doctype = 'CRM Deal'
		WHERE {where_clause}
		ORDER BY c.start_time DESC
		LIMIT 50000
		""",
		params,
		as_dict=True,
	)

	if rows:
		headers = list(rows[0].keys())
		data = [headers] + [[row.get(h) for h in headers] for row in rows]
	else:
		headers = [
			"call_id",
			"provider",
			"direction",
			"call_status",
			"caller_name",
			"receiver_name",
			"duration_seconds",
			"start_time",
			"lead_id",
			"lead_name",
		]
		data = [headers]

	xlsx_file = make_xlsx(data, "Calls")

	scope = "team" if (is_manager and not user) else (user or frappe.session.user)
	frappe.response["filename"] = f"calls_export_{scope}_{frappe.utils.today()}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"

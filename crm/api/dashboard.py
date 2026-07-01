import json

import frappe
from frappe import _
from frappe.query_builder import Case, DocType
from frappe.query_builder.functions import Avg, Coalesce, Count, Date, DateFormat, IfNull, Sum
from pypika.functions import Function

from crm.fcrm.doctype.crm_dashboard.crm_dashboard import create_default_manager_dashboard
from crm.overrides.crm_lead_permissions import downstream_users
from crm.permissions.role_config import TIER1_FULL_RW
from crm.utils import sales_user_only


def _visible_owners(user: str) -> set[str] | None:
	"""Return the set of ``lead_owner`` values ``user`` is allowed to see on
	the dashboard, or ``None`` for admin-tier roles that bypass the filter
	entirely.

	- Admin / System Manager → ``None`` (no SQL filter, sees every lead)
	- Everyone else → ``downstream_users(user)`` from CRM Sales Hierarchy
	  (self + all descendants).  Subordinates never see their parents.

	The returned set is sufficient to drive a ``lead_owner IN (…)`` clause.
	Callers should treat ``None`` as "skip the filter".
	"""
	roles = set(frappe.get_roles(user))
	if roles & TIER1_FULL_RW:
		return None
	return downstream_users(user)


def _scope_users(user: str, requested_users: list[str] | None = None) -> set[str] | None:
	"""Resolve which lead_owner values to filter on for ``user``'s dashboard.

	**Picks are literal** — selecting users in the picker filters to
	exactly those users' own leads, with no subtree expansion and no
	implicit self.  To include themselves, the caller must pick their
	own name from the dropdown explicitly.

	- No picks + admin → ``None`` (no filter; sees every lead)
	- No picks + non-admin → ``{user}`` (own leads only)
	- Picks + admin → exactly the picked users (empty picks reverts to all)
	- Picks + non-admin → picked users intersected with caller's visible
	  scope (info-leak guard).  If all picks fall outside scope (shouldn't
	  happen since the picker only shows in-scope users), falls back to
	  ``{user}`` so the dashboard isn't empty.
	"""
	visible = _visible_owners(user)

	if not requested_users:
		# No picks — default.
		return None if visible is None else {user}

	picks = {u for u in requested_users if u}

	if visible is None:
		# Admin — exactly the picks, no implicit self.
		return picks or None

	# Hierarchy-scoped caller — clamp picks to visible scope so the picker
	# can't be abused to peek outside the caller's subtree.
	clamped = picks & visible
	return clamped or {user}


def _owner_qb_filter(query, column, owners):
	"""Apply an ``IN``-clause for ``owners`` to a pypika query.

	- ``owners is None`` → no filter (admin)
	- ``owners == set()`` → always-false (no rows visible)
	- non-empty → ``column.isin(list(owners))``
	"""
	if owners is None:
		return query
	if not owners:
		# Match nothing — return a query that yields zero rows.
		return query.where(column.isnull() & column.isnotnull())
	return query.where(column.isin(list(owners)))


def _owner_sql_in(owners, alias: str = "lead_owner") -> str:
	"""Inline-escaped ``AND alias IN (…)`` fragment for f-string SQL.

	Splicing the fragment into the SQL string is simpler than threading
	params through every chart function.  ``owners`` originates from
	``downstream_users(self)`` (DB-backed) intersected with the caller's
	visible scope, so values are safe — and we still go through
	``frappe.db.escape`` as defense-in-depth.

	- ``owners is None`` → ``""`` (no filter; admin)
	- empty iterable → ``" AND 1 = 0"`` (always-false; nothing visible)
	- non-empty → ``" AND <alias> IN ('a','b',…)"``
	"""
	if owners is None:
		return ""
	if not owners:
		return " AND 1 = 0"
	quoted = ", ".join(frappe.db.escape(o) for o in owners)
	return f" AND {alias} IN ({quoted})"


def _coerce_users_arg(users) -> list[str]:
	"""Accept ``users`` as JSON-string (from HTTP form data), list, or scalar.

	Frappe's whitelisted endpoints pass list-shaped query params through as
	either ``["a", "b"]`` (already a list when called from Python) or a JSON
	string ``'["a", "b"]'`` (when posted as a form value).  Normalize.
	"""
	if not users:
		return []
	if isinstance(users, list):
		return [u for u in users if u]
	if isinstance(users, str):
		stripped = users.strip()
		if stripped.startswith("["):
			try:
				parsed = json.loads(stripped)
				if isinstance(parsed, list):
					return [u for u in parsed if u]
			except json.JSONDecodeError:
				pass
		return [stripped] if stripped else []
	return []


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
def get_dashboard(
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
	users: list[str] | str | None = None,
):
	"""Get the dashboard data, scoped to the caller's hierarchy.

	Filtering rules (replaces the legacy role-tier gating):

	- Admin / System Manager → see every lead, ``users`` is the only filter
	- Everyone else → automatically scoped to ``downstream_users(self)``
	  (self + descendants from CRM Sales Hierarchy).  A leaf user with no
	  reports sees only their own data.

	``user`` (single) is kept for backward compatibility with older
	frontend callers.  ``users`` (list) supersedes it — if both are
	supplied, ``users`` wins.
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Resolve list-of-users.  ``user`` (singular) is the legacy field; promote
	# it into the list form if ``users`` wasn't explicitly given.
	requested = _coerce_users_arg(users)
	if not requested and user:
		requested = [user]

	owners = _scope_users(frappe.session.user, requested)

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
			l["data"] = method(from_date, to_date, owners)
		else:
			l["data"] = None

	return layout


@frappe.whitelist()
@sales_user_only
def get_chart(
	name: str,
	type: str,
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
	users: list[str] | str | None = None,
):
	"""Get chart data for one chart, hierarchy-scoped to the caller.

	See ``get_dashboard`` for the visibility contract.  ``users`` (list) is
	the preferred filter input; ``user`` (single) stays for legacy callers.
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	requested = _coerce_users_arg(users)
	if not requested and user:
		requested = [user]

	owners = _scope_users(frappe.session.user, requested)

	method_name = f"get_{name}"
	if hasattr(frappe.get_attr("crm.api.dashboard"), method_name):
		method = getattr(frappe.get_attr("crm.api.dashboard"), method_name)
		return method(from_date, to_date, owners)
	else:
		return {"error": _("Invalid chart name")}


@frappe.whitelist()
@sales_user_only
def get_visible_users():
	"""Return users the caller can pick to filter the dashboard.

	Picks replace the default scope: ticking a user shows only that user's
	leads.  Self IS in the picker so the caller can explicitly include
	themselves alongside reportees (otherwise picking only reportees would
	exclude self).

	- Admin / System Manager → all Sales Users (entire org)
	- Hierarchy caller → ``downstream_users(self)`` (self + reportees)
	- Leaf user (no reports) → ``[]`` (picker hides — only ever sees own)
	"""
	caller = frappe.session.user
	owners = _visible_owners(caller)

	user_filters: dict = {"enabled": 1}
	if owners is not None:
		# Hierarchy-scoped — self + reportees.  Hide picker for leaf users.
		if owners == {caller}:
			return []
		user_filters["name"] = ["in", list(owners)]
	else:
		# Admin — show every Sales User in the system.
		has_role = frappe.db.get_all(
			"Has Role",
			filters={"role": "Sales User", "parenttype": "User"},
			pluck="parent",
		)
		if not has_role:
			return []
		user_filters["name"] = ["in", list(set(has_role))]

	return frappe.get_all(
		"User",
		filters=user_filters,
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
	)


def get_total_leads(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Lead.lead_owner.isin(list(owners))
			if owners
			else (Lead.lead_owner.isnull() & Lead.lead_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (Lead.creation >= prev_from_date) & (Lead.creation < from_date)
	if owners is not None:
		prev_cond = prev_cond & (
			Lead.lead_owner.isin(list(owners))
			if owners
			else (Lead.lead_owner.isnull() & Lead.lead_owner.isnotnull())
		)

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


def get_ongoing_deals(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (
		(Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type.notin(["Won", "Lost"]))
	)
	if owners is not None:
		prev_cond = prev_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_average_ongoing_deal_value(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (
		(Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type.notin(["Won", "Lost"]))
	)
	if owners is not None:
		prev_cond = prev_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_won_deals(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < from_date) & (Status.type == "Won")
	if owners is not None:
		prev_cond = prev_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_average_won_deal_value(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (Deal.closed_date >= prev_from_date) & (Deal.closed_date < from_date) & (Status.type == "Won")
	if owners is not None:
		prev_cond = prev_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_average_deal_value(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		current_cond = current_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

	# Build conditions for previous period
	prev_cond = (Deal.creation >= prev_from_date) & (Deal.creation < from_date) & (Status.type != "Lost")
	if owners is not None:
		prev_cond = prev_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_average_time_to_close_a_lead(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		base_cond = base_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_average_time_to_close_a_deal(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	if owners is not None:
		base_cond = base_cond & (
			Deal.deal_owner.isin(list(owners))
			if owners
			else (Deal.deal_owner.isnull() & Deal.deal_owner.isnotnull())
		)

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


def get_sales_trend(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	leads_query = _owner_qb_filter(leads_query, Lead.lead_owner, owners)

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

	deals_query = _owner_qb_filter(deals_query, Deal.deal_owner, owners)

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


def get_forecasted_revenue(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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


def get_funnel_conversion(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	lead_conds += _owner_sql_in(owners, "lead_owner")

	result = []

	# Get total leads using Query Builder
	CRMLead = DocType("CRM Lead")

	query = (
		frappe.qb.from_(CRMLead)
		.select(Count("*").as_("count"))
		.where(Date(CRMLead.creation).between(from_date, to_date))
	)

	query = _owner_qb_filter(query, CRMLead.lead_owner, owners)

	total_leads = query.run(as_dict=True)
	total_leads_count = total_leads[0].count if total_leads else 0

	result.append({"stage": "Leads", "count": total_leads_count})

	result += get_deal_status_change_counts(from_date, to_date, owners)

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


def get_deals_by_stage_axis(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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


def get_deals_by_stage_donut(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by stage"),
		"subtitle": _("Current pipeline distribution"),
		"categoryColumn": "stage",
		"valueColumn": "count",
	}


def get_lost_deal_reasons(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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


def get_leads_by_source(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMLead.lead_owner, owners)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Leads by source"),
		"subtitle": _("Lead generation channel analysis"),
		"categoryColumn": "source",
		"valueColumn": "count",
	}


def get_deals_by_source(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

	result = query.run(as_dict=True)

	return {
		"data": result or [],
		"title": _("Deals by source"),
		"subtitle": _("Deal generation channel analysis"),
		"categoryColumn": "source",
		"valueColumn": "count",
	}


def get_deals_by_territory(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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


def get_deals_by_salesperson(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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
	owners=None,
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

	query = _owner_qb_filter(query, CRMDeal.deal_owner, owners)

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


# Acronyms that should stay fully uppercase in Excel headers instead of
# title-cased. Add new entries as data shape evolves.
_HEADER_ACRONYMS = frozenset(
	{
		"id",
		"sid",
		"url",
		"utm",
		"sla",
		"crm",
		"sqft",
		"c0",
		"c1",
		"c2",
		"c3",
		"c4",
		"c5",
		"c6",
		"c7",
		"qs",
	}
)


def _humanize_header(key: str) -> str:
	"""Convert a snake_case column key to a human-readable Excel header.

	lead_id              -> "Lead ID"
	custom_c0_entered_on -> "C0 Entered On"   (strips the `custom_` prefix
	                                           since it's a Frappe naming
	                                           convention not meaningful
	                                           to end users)
	utm_source           -> "UTM Source"
	provider_call_sid    -> "Provider Call SID"
	recording_url        -> "Recording URL"
	"""
	if key.startswith("custom_"):
		key = key[len("custom_") :]
	return " ".join(
		word.upper() if word.lower() in _HEADER_ACRONYMS else word.capitalize() for word in key.split("_")
	)


# ─── 11.1 Lead Generation Performance ────────────────────────────────


def get_leads_over_time(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Daily lead creation trend."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = _owner_sql_in(owners)
	params = {"from_date": from_date, "to_date": to_date}

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


# ─── Quotes Sent over time ──────────────────────────────────────────
# "Quote sent" is proxied by the date the Quote Request was created — a
# QR is created the moment a sales user requests a quote with intent to
# send it to the customer.  Estimation typically completes within 24h, so
# creation date ≈ "quote going to customer" for analytics purposes.
#
# Hierarchy scope joins back to the parent CRM Lead's lead_owner (NOT the
# requested_by user on the QR itself — that distinction matters when a
# manager requests a quote on behalf of a rep).


def get_quotes_sent_over_time(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Daily quote-request creation count, scoped by parent lead's owner."""
	from_date, to_date = _date_window(from_date, to_date)

	# Join QR → CRM Lead to filter on lead_owner instead of QR.requested_by.
	owner_clause = _owner_sql_in(owners, "l.lead_owner")
	params = {"from_date": from_date, "to_date": to_date}

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT DATE(qr.creation) AS date, COUNT(*) AS quotes
		FROM `tabCRM Quote Request` qr
		LEFT JOIN `tabCRM Lead` l ON l.name = qr.lead
		WHERE DATE(qr.creation) BETWEEN %(from_date)s AND %(to_date)s
		      {owner_clause}
		GROUP BY DATE(qr.creation)
		ORDER BY DATE(qr.creation)
		""",
		params,
		as_dict=True,
	)

	data = [{"date": frappe.utils.formatdate(r.date, "yyyy-MM-dd"), "quotes": r.quotes} for r in rows]

	return {
		"data": data,
		"title": _("Quotes Sent"),
		"subtitle": _("Daily quote requests created"),
		"xAxis": {"title": _("Date"), "key": "date", "type": "time", "timeGrain": "day"},
		"yAxis": {"title": _("Quotes")},
		"series": [{"name": "quotes", "type": "line", "showDataPoints": True}],
	}


# ─── Orders Won over time ───────────────────────────────────────────
# Counts leads whose status transitioned to C4 (Won) — bucketed by
# the dedicated ``custom_c4_entered_on`` Datetime field on CRM Lead
# (set by the "Stage Side Effects" server script on the C2→C4 save).


def get_orders_won_over_time(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Daily count of leads reaching C4 (Won), bucketed by C4 entry date."""
	from_date, to_date = _date_window(from_date, to_date)

	owner_clause = _owner_sql_in(owners, "lead_owner")
	params = {"from_date": from_date, "to_date": to_date}

	rows = frappe.db.sql(  # nosemgrep
		f"""
		SELECT DATE(custom_c4_entered_on) AS date, COUNT(*) AS orders
		FROM `tabCRM Lead`
		WHERE custom_c4_entered_on IS NOT NULL
		      AND DATE(custom_c4_entered_on) BETWEEN %(from_date)s AND %(to_date)s
		      AND status = 'C4'
		      {owner_clause}
		GROUP BY DATE(custom_c4_entered_on)
		ORDER BY DATE(custom_c4_entered_on)
		""",
		params,
		as_dict=True,
	)

	data = [{"date": frappe.utils.formatdate(r.date, "yyyy-MM-dd"), "orders": r.orders} for r in rows]

	return {
		"data": data,
		"title": _("Orders Won"),
		"subtitle": _("Daily count of leads entering C4 (Won)"),
		"xAxis": {"title": _("Date"), "key": "date", "type": "time", "timeGrain": "day"},
		"yAxis": {"title": _("Orders")},
		"series": [{"name": "orders", "type": "line", "showDataPoints": True}],
	}


def get_leads_by_sub_source(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	query = _owner_qb_filter(query, CRMLead.lead_owner, owners)

	return {
		"data": query.run(as_dict=True) or [],
		"title": _("Leads by sub-source"),
		"subtitle": _("Marketing channel sub-attribution"),
		"xAxis": {"title": _("Sub-source"), "key": "sub_source", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


def get_leads_by_source_axis(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	query = _owner_qb_filter(query, CRMLead.lead_owner, owners)

	return {
		"data": query.run(as_dict=True) or [],
		"title": _("Leads by source"),
		"subtitle": _("Lead generation channel — bar view"),
		"xAxis": {"title": _("Source"), "key": "source", "type": "category"},
		"yAxis": {"title": _("Leads")},
		"swapXY": True,
		"series": [{"name": "count", "type": "bar", "echartOptions": {"colorBy": "data"}}],
	}


def get_lead_spotting_productivity(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	user_filter = _owner_sql_in(owners, "l.lead_owner")

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


def get_lead_pipeline_funnel(from_date: str | None = None, to_date: str | None = None, owners=None):
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

	user_filter = _owner_sql_in(owners)
	params = {"from_date": from_date, "to_date": to_date}

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


def get_c2_sub_status_breakdown(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""For leads currently in C2, distribution by lead_status (engagement)."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = _owner_sql_in(owners)
	params = {"from_date": from_date, "to_date": to_date}

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


def get_calls_per_caller(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Call volume per caller across all telephony providers."""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	user_filter = _owner_sql_in(owners, "c.caller")

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


def get_call_dispositions(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Disposition distribution across all calls in the date window."""
	from_date, to_date = _date_window(from_date, to_date)

	params = {"from_date": from_date, "to_date": to_date}
	# Calls are "yours" if caller OR receiver is in scope.
	caller_in = _owner_sql_in(owners, "caller")
	receiver_in = _owner_sql_in(owners, "receiver")
	if caller_in and receiver_in:
		# Both non-empty IN clauses — combine with OR; strip leading " AND "
		user_filter = " AND (" + caller_in[5:] + " OR " + receiver_in[5:] + ")"
	else:
		user_filter = ""

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


def get_avg_c0_to_c2_time_per_caller(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Average days from C0 entry → C2 entry, grouped by lead_owner.

	Only counts leads where BOTH custom_c0_entered_on and
	custom_c2_entered_on are populated (the After Save script set them).
	Primary effectiveness metric per spec §11.3.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = ""
	params = {"from_date": from_date, "to_date": to_date}
	user_filter = _owner_sql_in(owners, "l.lead_owner")

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


def get_lost_lead_reasons(from_date: str | None = None, to_date: str | None = None, owners=None):
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
	query = _owner_qb_filter(query, CRMLead.lead_owner, owners)

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


def get_leads_by_geography(from_date: str | None = None, to_date: str | None = None, owners=None):
	"""Top 20 cities by lead volume, with conversion (Won/total) ratio.

	Note: this is a tabular/bar view of geography. A true heatmap on a
	map tile layer requires a frontend library (Leaflet) — to be added
	in a follow-up. The data here is sufficient to drive both.
	"""
	from_date, to_date = _date_window(from_date, to_date)

	user_filter = _owner_sql_in(owners)
	params = {"from_date": from_date, "to_date": to_date}

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
	owners: list[str] | str | None = None,
):
	"""Excel export of leads with linked deal/quote/activity summary.

	Hierarchy-scoped (same contract as ``get_dashboard``):

	- Admin / System Manager → all leads, optional ``owners`` narrows the set
	- Everyone else → only leads owned by users in ``downstream_users(self)``;
	  ``owners`` further narrows (intersected with the visible set)

	Empty result still returns a headers-only workbook so the browser gets
	a clean file instead of an error page.
	"""
	from frappe.utils.xlsxutils import make_xlsx

	scoped_owners = _scope_users(frappe.session.user, _coerce_users_arg(owners))

	conditions = ["l.status IS NOT NULL"]
	params: dict = {}

	if from_date and to_date:
		conditions.append("l.creation >= %(from_date)s")
		conditions.append("l.creation < %(to_date_exclusive)s")
		params["from_date"] = from_date
		params["to_date_exclusive"] = frappe.utils.add_days(to_date, 1)

	# Hierarchy filter: leads owned by anyone in the caller's scope.  Admin
	# (scoped_owners is None) gets no filter and sees every lead.
	owner_clause = _owner_sql_in(scoped_owners, "l.lead_owner")
	if owner_clause:
		# Strip the leading " AND " — we'll AND it back in via the conditions list.
		conditions.append(owner_clause[5:])

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
			l._assign                           AS assigned_to,
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
		keys = list(rows[0].keys())
		headers = [_humanize_header(k) for k in keys]
		data = [headers] + [[row.get(k) for k in keys] for row in rows]
	else:
		# Empty result: emit headers-only workbook so the browser gets a file,
		# not a JSON error page (download was initiated via window.location).
		keys = [
			"lead_id",
			"lead_name",
			"stage",
			"customer_type",
			"lead_owner",
			"created_on",
		]
		data = [[_humanize_header(k) for k in keys]]

	xlsx_file = make_xlsx(data, "Leads")

	# Filename: "team" when admin sees everything, "filtered" when narrowed,
	# else the caller's email (single-scope leaf user).
	if scoped_owners is None:
		scope = "all"
	elif len(scoped_owners) == 1 and frappe.session.user in scoped_owners:
		scope = frappe.session.user
	else:
		scope = "team"
	frappe.response["filename"] = f"lead_export_{scope}_{frappe.utils.today()}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"


@frappe.whitelist()
@sales_user_only
def download_calls_export(
	from_date: str | None = None,
	to_date: str | None = None,
	owners: list[str] | str | None = None,
):
	"""Excel export of CRM Call Log rows across all telephony providers.

	Hierarchy-scoped (same contract as ``get_dashboard``):

	- Admin / System Manager → every call, optional ``owners`` narrows the
	  set by caller OR receiver match
	- Everyone else → calls where caller or receiver is in the caller's
	  ``downstream_users``; ``owners`` further narrows

	One row per call. Includes lead context when the call is attached to
	a CRM Lead. Date filter applies to call start_time.
	"""
	from frappe.utils.xlsxutils import make_xlsx

	scoped_owners = _scope_users(frappe.session.user, _coerce_users_arg(owners))

	conditions = ["c.start_time IS NOT NULL"]
	params: dict = {}

	if from_date and to_date:
		conditions.append("c.start_time >= %(from_date)s")
		conditions.append("c.start_time < %(to_date_exclusive)s")
		params["from_date"] = from_date
		params["to_date_exclusive"] = frappe.utils.add_days(to_date, 1)

	# Calls are scoped by caller OR receiver match (a call is "yours" if
	# you placed it or answered it).  Admin (scoped_owners is None) skips
	# the filter entirely.
	caller_clause = _owner_sql_in(scoped_owners, "c.caller")
	receiver_clause = _owner_sql_in(scoped_owners, "c.receiver")
	if caller_clause and receiver_clause:
		# Strip leading " AND " from both and OR them together.
		conditions.append(f"({caller_clause[5:]} OR {receiver_clause[5:]})")

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
		keys = list(rows[0].keys())
		headers = [_humanize_header(k) for k in keys]
		data = [headers] + [[row.get(k) for k in keys] for row in rows]
	else:
		keys = [
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
		data = [[_humanize_header(k) for k in keys]]

	xlsx_file = make_xlsx(data, "Calls")

	if scoped_owners is None:
		scope = "all"
	elif len(scoped_owners) == 1 and frappe.session.user in scoped_owners:
		scope = frappe.session.user
	else:
		scope = "team"
	frappe.response["filename"] = f"calls_export_{scope}_{frappe.utils.today()}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"

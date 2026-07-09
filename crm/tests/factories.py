"""Shared test factories for the new-flow test suite.

Each helper returns the inserted Document (or its name) and uses
``ignore_permissions=True`` to bypass role checks. Server-script
side-effects fire normally — wrap calling tests in a transaction
(``FrappeTestCase``) so they get rolled back.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import today


def make_lead(
	*,
	phone: str = "+919800000001",
	first_name: str = "Test",
	last_name: str = "Lead",
	email: str | None = None,
	status: str = "C0",
	lead_status: str = "Active",
	source: str = "Direct",
	**extra: Any,
) -> Any:
	"""Insert a CRM Lead with sane defaults.

	To set ``lead_status`` to something other than the doctype default,
	we set it via ``frappe.db.set_value`` *after* insert because the
	property setter at ``install.py:348-357`` makes the field read-only
	on new documents (``eval:!doc.name``). Production code does the same
	(see ``retry_engine.py:205``).
	"""
	payload: dict[str, Any] = {
		"doctype": "CRM Lead",
		"first_name": first_name,
		"last_name": last_name,
		"mobile_no": phone,
		"status": status,
		"source": source,
	}
	if email:
		payload["email"] = email
	payload.update(extra)

	lead = frappe.get_doc(payload).insert(ignore_permissions=True)

	if lead_status and lead_status != "Active":
		frappe.db.set_value("CRM Lead", lead.name, "lead_status", lead_status)
		lead.reload()

	return lead


def make_call_log(
	*,
	lead_name: str,
	status: str = "Completed",
	disposition: str | None = None,
	caller: str | None = None,
	receiver: str | None = None,
	from_number: str = "+919800000099",
	to_number: str = "+919800000001",
	call_type: str = "Incoming",
	**extra: Any,
) -> Any:
	"""Insert a CRM Call Log linked to the given lead via reference_docname."""
	payload: dict[str, Any] = {
		"doctype": "CRM Call Log",
		"id": frappe.generate_hash(length=10),
		"from": from_number,
		"to": to_number,
		"type": call_type,
		"status": status,
		"medium": "Manual",
		"reference_doctype": "CRM Lead",
		"reference_docname": lead_name,
	}
	if disposition:
		payload["disposition"] = disposition
	if caller:
		payload["caller"] = caller
	if receiver:
		payload["receiver"] = receiver
	payload.update(extra)
	return frappe.get_doc(payload).insert(ignore_permissions=True)


def make_retry_log(
	*,
	lead_name: str,
	day: int = 1,
	attempt_count: int = 0,
	status: str = "Active",
	next_attempt_date: str | None = None,
	last_attempt_date: str | None = None,
) -> Any:
	"""Insert a CRM Retry Log row directly (bypassing register_no_answer's guard)."""
	return frappe.get_doc(
		{
			"doctype": "CRM Retry Log",
			"lead": lead_name,
			"status": status,
			"day_in_sequence": day,
			"attempt_count": attempt_count,
			"last_attempt_date": last_attempt_date or today(),
			"next_attempt_date": next_attempt_date or today(),
		}
	).insert(ignore_permissions=True)


def make_hierarchy_node(
	*,
	user: str,
	reports_to: str | None = None,
	is_group: int = 0,
) -> Any:
	"""Insert (or fetch) a CRM Sales Hierarchy node for ``user``.

	``reports_to`` is the *email* of the parent's user; we resolve it to the
	parent node's name. Tests that build a chain should call this for each
	user from the root down, then call ``rebuild_tree("CRM Sales Hierarchy")``
	once at the end so ``lft / rgt`` are populated for the
	``downstream_users`` range scan.
	"""
	existing = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, "name")
	if existing:
		return frappe.get_doc("CRM Sales Hierarchy", existing)

	parent_name = None
	if reports_to:
		parent_name = frappe.db.get_value("CRM Sales Hierarchy", {"user": reports_to}, "name")

	return frappe.get_doc(
		{
			"doctype": "CRM Sales Hierarchy",
			"user": user,
			"reports_to": parent_name,
			"is_group": is_group,
		}
	).insert(ignore_permissions=True)


def make_user(
	*,
	email: str,
	roles: list[str] | None = None,
	role_profile: str | None = None,
	full_name: str = "Test User",
) -> Any:
	"""Create or fetch a Frappe User with the given roles / role profile.

	Pass ``role_profile=<name>`` to assign one of the seeded CRM role profiles
	(e.g. ``"Sales Head"``, ``"ASM"``) — this is the production path. The
	``roles=[...]`` kwarg is retained for tests that want to hand-pick a role
	combination (or layer extra roles on top of a profile).
	"""
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		first_name, _, last_name = full_name.partition(" ")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name or "Test",
				"last_name": last_name or "",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

	dirty = False
	if role_profile and user.role_profile_name != role_profile:
		user.role_profile_name = role_profile
		dirty = True

	if roles:
		existing = {r.role for r in user.get("roles") or []}
		for role in roles:
			if role not in existing:
				user.append("roles", {"role": role})
				dirty = True

	if dirty:
		user.save(ignore_permissions=True)

	return user

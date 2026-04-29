import frappe

from crm.integrations.aisensy.aisensy_handler import (
	is_aisensy_enabled as _is_enabled,
)
from crm.integrations.aisensy.aisensy_handler import (
	send_template_message,
)


@frappe.whitelist()
def is_aisensy_enabled() -> bool:
	return _is_enabled()


@frappe.whitelist()
def get_messages(reference_doctype: str, reference_name: str) -> list:
	return frappe.get_all(
		"CRM AISensy Message",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		fields=[
			"name",
			"to",
			"template_name",
			"variables",
			"status",
			"message_id",
			"creation",
		],
		order_by="creation asc",
	)


@frappe.whitelist()
def send_message(
	reference_doctype: str,
	reference_name: str,
	to: str,
	template_name: str,
	variables: list | None = None,
) -> dict:
	if not _is_enabled():
		frappe.throw(frappe._("AISensy integration is not enabled."))
	return send_template_message(
		to=to,
		template_name=template_name,
		variables=variables or [],
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

import frappe


def on_desk_edit(doc, method=None):
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	frappe.logger("desk_audit").info(f"[DESK EDIT] {doc.doctype}: {doc.name} | user={frappe.session.user}")

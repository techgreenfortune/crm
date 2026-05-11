import frappe


@frappe.whitelist()
def send_retry_whatsapp(lead_name: str, day: int) -> None:
	"""Send AiSensy follow-up message. Called via frappe.enqueue from Scheduler Event Server Script."""

	# Re-check: small window between scheduler enqueue and worker pickup
	current_status = frappe.db.get_value("CRM Lead", lead_name, "status")
	if current_status not in ("C0", "Cold"):
		frappe.logger().info(
			f"[RetryEngine] Lead {lead_name} moved out of C0/Cold — skipping Day {day} WhatsApp"
		)
		return

	from crm.integrations.aisensy.aisensy_handler import (
		get_aisensy_settings,
		is_aisensy_enabled,
		send_template_message,
	)

	if not is_aisensy_enabled():
		frappe.logger().info(f"[RetryEngine] AiSensy disabled — skipping Day {day} WhatsApp for {lead_name}")
		return

	settings = get_aisensy_settings()
	template_name = getattr(settings, "retry_followup_template", None)
	if not template_name:
		frappe.logger().info(
			f"[RetryEngine] retry_followup_template not configured — skipping Day {day} WhatsApp for {lead_name}"
		)
		return

	lead = frappe.db.get_value("CRM Lead", lead_name, ["mobile_no", "lead_name"], as_dict=True)
	if not lead:
		return

	phone = lead.get("mobile_no") or ""
	if not phone:
		frappe.logger().info(f"[RetryEngine] No mobile_no on lead {lead_name} — skipping WhatsApp")
		return

	try:
		send_template_message(
			to=phone,
			template_name=template_name,
			variables=[lead.get("lead_name") or lead_name],
			reference_doctype="CRM Lead",
			reference_name=lead_name,
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"RetryEngine: AiSensy send failed for lead {lead_name} (template: {template_name})",
		)

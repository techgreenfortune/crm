import frappe
from frappe.utils import cint

from crm.integrations.brevo.brevo_handler import is_brevo_enabled
from crm.integrations.brevo.brevo_handler import send_email as _brevo_send


@frappe.whitelist()
def make(
	doctype: str | None = None,
	name: str | None = None,
	content: str | None = None,
	subject: str | None = None,
	sent_or_received: str = "Sent",
	sender: str | None = None,
	sender_full_name: str | None = None,
	recipients: str | list | None = None,
	communication_medium: str = "Email",
	send_email: bool | int = False,
	print_html: str | None = None,
	print_format: str | None = None,
	attachments: str | list | None = None,
	send_me_a_copy: bool | int = False,
	cc: str | None = None,
	bcc: str | None = None,
	read_receipt: bool | int | None = None,
	print_letterhead: bool | int = True,
	email_template: str | None = None,
	communication_type: str | None = None,
	send_after: str | None = None,
	print_language: str | None = None,
	now: bool = False,
	in_reply_to: str | None = None,
	**kwargs,
) -> dict:
	from frappe.core.doctype.communication.email import make as original_make

	should_send = cint(send_email)

	if not should_send or not is_brevo_enabled():
		return original_make(
			doctype=doctype,
			name=name,
			content=content,
			subject=subject,
			sent_or_received=sent_or_received,
			sender=sender,
			sender_full_name=sender_full_name,
			recipients=recipients,
			communication_medium=communication_medium,
			send_email=send_email,
			print_html=print_html,
			print_format=print_format,
			attachments=attachments,
			send_me_a_copy=send_me_a_copy,
			cc=cc,
			bcc=bcc,
			read_receipt=read_receipt,
			print_letterhead=print_letterhead,
			email_template=email_template,
			communication_type=communication_type,
			send_after=send_after,
			print_language=print_language,
			now=now,
			in_reply_to=in_reply_to,
			**kwargs,
		)

	result = original_make(
		doctype=doctype,
		name=name,
		content=content,
		subject=subject,
		sent_or_received=sent_or_received,
		sender=sender,
		sender_full_name=sender_full_name,
		recipients=recipients,
		communication_medium=communication_medium,
		send_email=False,
		print_html=print_html,
		print_format=print_format,
		attachments=attachments,
		send_me_a_copy=send_me_a_copy,
		cc=cc,
		bcc=bcc,
		read_receipt=read_receipt,
		print_letterhead=print_letterhead,
		email_template=email_template,
		communication_type=communication_type,
		send_after=send_after,
		print_language=print_language,
		now=now,
		in_reply_to=in_reply_to,
		**kwargs,
	)

	all_recipients = []
	if isinstance(recipients, list):
		all_recipients.extend(recipients)
	elif recipients:
		all_recipients.extend([r.strip() for r in recipients.split(",") if r.strip()])

	if cint(send_me_a_copy) and sender:
		sender_email = sender.split("<")[-1].rstrip(">").strip() if "<" in sender else sender
		if sender_email and sender_email not in all_recipients:
			all_recipients.append(sender_email)

	if all_recipients:
		try:
			_brevo_send(
				recipients=all_recipients,
				subject=subject or "",
				html_content=content or "",
			)
		except Exception:
			frappe.log_error(
				title="Brevo Communication Send Error",
				message=frappe.get_traceback(),
			)

	return result

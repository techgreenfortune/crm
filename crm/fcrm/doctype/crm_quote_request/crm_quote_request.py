import frappe
from frappe import _
from frappe.model.document import Document

from crm.permissions.role_config import REVIEWER_ROLES, TIER1_FULL_RW

# Estimation Team uploads quotes for any lead (including closed ones), so they
# bypass the lead-scoped filter on top of the tier-1 bypasses.
_QR_BYPASS_ROLES = TIER1_FULL_RW | {"Estimation Team"}


def get_permission_query_conditions(user=None):
	"""List-level filter for CRM Quote Request.

	Wired via permission_query_conditions["CRM Quote Request"] in hooks.py.

	- Admin / System Manager / Sales Head / Sales Coordinator: see all
	- Estimation Team: see all (they upload quotes for any lead, incl. closed ones)
	- Everyone else: only QRs whose parent lead they own; ASM/RSM additionally
	  see QRs for leads owned by their downstream hierarchy.

	Mirrors the pure-hierarchy CRM Lead visibility — QR access follows
	``lead_owner`` (self + downstream subtree). Multi-assignee (``_assign``) was
	retired, so the old JSON-LIKE assignee branch is gone.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & _QR_BYPASS_ROLES:
		return ""

	esc = frappe.db.escape

	owners = {user}
	if roles & {"ASM", "RSM"}:
		from crm.overrides.crm_lead_permissions import downstream_users

		owners |= downstream_users(user)

	in_list = ", ".join(esc(u) for u in sorted(owners))

	return f"""EXISTS (
		SELECT 1 FROM `tabCRM Lead`
		WHERE `tabCRM Lead`.name = `tabCRM Quote Request`.lead
		AND `tabCRM Lead`.lead_owner IN ({in_list})
	)"""


class CRMQuoteRequest(Document):
	def before_insert(self):
		self.requested_by = frappe.session.user
		self.requested_on = frappe.utils.today()

	def before_validate(self):
		self._check_superseded()
		self._auto_promote_status()
		self._validate_positive_quote_values()

	def validate(self):
		self._guard_status_transition()
		if self.status == "Revision Requested" and not (self.notes or "").strip():
			frappe.throw(
				_("Notes are required when requesting a revision."),
				title=_("Notes Required"),
			)

	def before_save(self):
		self._guard_estimation_team_on_received()
		self._record_transition_trail()

	def on_update(self):
		self._sync_tentative_value_to_lead()
		if self.has_value_changed("status") and self.status == "Quote Received":
			try:
				frappe.get_doc("Notification", "Quote Received — Review & Share").send(self)
			except Exception:
				frappe.log_error(title="Quote Received notification failed", message=frappe.get_traceback())

	def _check_superseded(self):
		if self.is_superseded:
			frappe.throw(_("This quote request has been superseded and cannot be modified."))

	def _auto_promote_status(self):
		old = self.get_doc_before_save()
		old_status = old.status if old else None
		if (
			old is not None
			and old_status in ("Pending", "Revision Requested")
			and self.status == old_status
			and self.quote_file
		):
			self.status = "Quote Received"

	def _validate_positive_quote_values(self):
		if self.status == "Pending":
			return
		if not self.quote_value or self.quote_value <= 0:
			frappe.throw(_("Quote Value must be greater than 0."), title=_("Quote Value Required"))
		if not self.quote_margin or self.quote_margin <= 0:
			frappe.throw(_("Quote Margin must be greater than 0."), title=_("Quote Margin Required"))
		if not self.quote_sq_ft or self.quote_sq_ft <= 0:
			frappe.throw(_("Quote Area (sqft) must be greater than 0."), title=_("Quote Area Required"))
		if not self.quote_number or not str(self.quote_number).strip():
			frappe.throw(_("External Quote No. is required."), title=_("Quote Number Required"))
		if not self.total_quantity or self.total_quantity <= 0:
			frappe.throw(_("Total Quantity must be greater than 0."), title=_("Total Quantity Required"))

	def _guard_estimation_team_on_received(self):
		"""Freeze the QR for Estimation Team once status = Quote Received."""
		old = self.get_doc_before_save()
		if old is None:
			return
		if old.status != "Quote Received":
			return
		user = frappe.session.user
		if user == "Administrator":
			return
		roles = set(frappe.get_roles(user))
		if "Estimation Team" in roles and not (roles & TIER1_FULL_RW):
			frappe.throw(
				_("The quote has already been submitted. Estimation Team cannot edit it further."),
				frappe.PermissionError,
			)

	def _record_transition_trail(self):
		old = self.get_doc_before_save()
		old_status = old.status if old else None
		new_status = self.status

		if old is None or old_status == new_status:
			return
		if new_status not in ("Quote Received", "Revision Requested", "Accepted"):
			return

		if new_status == "Quote Received":
			parts = []
			if self.quote_value:
				parts.append(f"Value: {self.quote_value}")
			if self.quote_margin:
				parts.append(f"Margin: {self.quote_margin}%")
			meta = ", ".join(parts) if parts else "Quote uploaded."
			file_link = (
				f" — <a href='{self.quote_file}' target='_blank' rel='noopener'>View File</a>"
				if self.quote_file
				else ""
			)
			content = f"[AUTOMATION] Quote uploaded — {meta}{file_link}"
		elif new_status == "Revision Requested":
			now = frappe.utils.now_datetime()
			for row in self.images or []:
				if not row.uploaded_by:
					row.uploaded_by = frappe.session.user
				if not row.uploaded_on:
					row.uploaded_on = now
			reason = self.notes or "(no reason provided)"
			img_count = len(self.images or [])
			suffix = f" ({img_count} image(s) attached)" if img_count else ""
			content = f"[AUTOMATION] Quote revision requested: {reason}{suffix}"
		else:
			content = (
				"[AUTOMATION] Quote Accepted. Sales can now record the advance payment "
				"and move the lead to C4 (Won)."
			)

		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "CRM Lead",
					"reference_name": self.lead,
					"content": content,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				message=f"Failed to insert transition comment on QR {self.name} (lead={self.lead})",
				title="QR transition comment failed",
			)

		if new_status == "Quote Received" and self.quote_file and self.lead:
			qr_file = frappe.db.get_value(
				"File",
				{
					"attached_to_doctype": "CRM Quote Request",
					"attached_to_name": self.name,
					"file_url": self.quote_file,
				},
				"name",
			)
			already_mirrored = frappe.db.exists(
				"File",
				{
					"attached_to_doctype": "CRM Lead",
					"attached_to_name": self.lead,
					"file_url": self.quote_file,
				},
			)
			if qr_file and not already_mirrored:
				try:
					frappe.get_doc("File", qr_file).create_attachment_copy(
						attached_to_doctype="CRM Lead",
						attached_to_name=self.lead,
						ignore_permissions=True,
					)
				except Exception:
					frappe.log_error(
						message=f"Failed to mirror {self.quote_file} from QR {self.name} to lead {self.lead}",
						title="Quote file mirror to Lead failed",
					)

		if new_status == "Revision Requested":
			try:
				new_qr = frappe.new_doc("CRM Quote Request")
				new_qr.update({"lead": self.lead, "status": "Pending"})
				new_qr.flags.ignore_mandatory = True
				new_qr.insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(
					message=f"Failed to create successor QR for revision on {self.name}",
					title="Quote Revision QR Creation Failed",
				)
				frappe.throw(
					_("Could not create a new Quote Request for the revision round. Please try again.")
				)
			self.is_superseded = 1
			reason = self.notes or "(no reason provided)"
			try:
				if not frappe.db.exists(
					"CRM Task",
					{
						"reference_doctype": "CRM Lead",
						"reference_docname": self.lead,
						"task_type": "upload_quote",
						"status": ["in", ["Todo", "In Progress"]],
					},
				):
					frappe.get_doc(
						{
							"doctype": "CRM Task",
							"task_type": "upload_quote",
							"title": f"Upload Revised Quote — {self.lead_name or self.lead}",
							"status": "Todo",
							"priority": "High",
							"due_date": frappe.utils.add_days(frappe.utils.today(), 2),
							"reference_doctype": "CRM Lead",
							"reference_docname": self.lead,
							"quote_request": new_qr.name,
							"description": (
								f"Revision requested for lead {self.lead_name or self.lead}: {reason}. "
								f"Open Quote Request {new_qr.name} to upload the revised quote."
							),
						}
					).insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(
					message=f"Failed to create upload_quote task for revision on {self.name}",
					title="Quote Revision Task Creation Failed",
				)

			if self.images and self.lead:
				for row in self.images:
					if not row.image:
						continue
					already_mirrored = frappe.db.exists(
						"File",
						{
							"attached_to_doctype": "CRM Lead",
							"attached_to_name": self.lead,
							"file_url": row.image,
						},
					)
					if already_mirrored:
						continue
					qr_file = frappe.db.get_value(
						"File",
						{
							"attached_to_doctype": "CRM Quote Request",
							"attached_to_name": self.name,
							"file_url": row.image,
						},
						"name",
					)
					if qr_file:
						try:
							frappe.get_doc("File", qr_file).create_attachment_copy(
								attached_to_doctype="CRM Lead",
								attached_to_name=self.lead,
								ignore_permissions=True,
							)
						except Exception:
							frappe.log_error(
								message=f"Failed to mirror image {row.image} from QR {self.name} to lead {self.lead}",
								title="Revision image mirror to Lead failed",
							)

	def _sync_tentative_value_to_lead(self):
		old = self.get_doc_before_save()
		old_status = old.status if old else None

		if old_status != self.status and self.status == "Quote Received":
			updates = {}
			if self.quote_value:
				updates["custom_tentative_value"] = self.quote_value
				updates["custom_final_price"] = self.quote_value
			if self.quote_margin:
				updates["custom_final_margin"] = self.quote_margin
			if self.quote_file:
				updates["custom_final_quote"] = self.quote_file
			if self.quote_sq_ft:
				updates["custom_tentative_area_sqft"] = self.quote_sq_ft
			if updates:
				frappe.db.set_value("CRM Lead", self.lead, updates)

			open_upload_task = frappe.db.get_value(
				"CRM Task",
				{
					"reference_doctype": "CRM Lead",
					"reference_docname": self.lead,
					"task_type": "upload_quote",
					"status": ["in", ["Todo", "In Progress"]],
				},
				"name",
			)
			if open_upload_task:
				frappe.db.set_value("CRM Task", open_upload_task, "status", "Done")

			lead_owner = frappe.db.get_value("CRM Lead", self.lead, "lead_owner")
			if not frappe.db.exists(
				"CRM Task",
				{
					"reference_doctype": "CRM Lead",
					"reference_docname": self.lead,
					"task_type": "review_quote",
					"status": ["in", ["Todo", "In Progress"]],
				},
			):
				review_task = {
					"doctype": "CRM Task",
					"task_type": "review_quote",
					"title": f"Review & Share Quote — {self.lead_name or self.lead}",
					"status": "Todo",
					"priority": "High",
					"due_date": frappe.utils.add_days(frappe.utils.today(), 1),
					"reference_doctype": "CRM Lead",
					"reference_docname": self.lead,
					"quote_request": self.name,
					"description": (
						f"Quote received for {self.lead_name or self.lead} "
						f"(Value: {self.quote_value}). Review the quotation and share it with the customer."
					),
				}
				if lead_owner:
					review_task["assigned_to"] = lead_owner
				frappe.get_doc(review_task).insert(ignore_permissions=True)

		if old_status != self.status and self.status == "Revision Requested":
			open_review_task = frappe.db.get_value(
				"CRM Task",
				{
					"reference_doctype": "CRM Lead",
					"reference_docname": self.lead,
					"task_type": "review_quote",
					"status": ["in", ["Todo", "In Progress"]],
				},
				"name",
			)
			if open_review_task:
				frappe.db.set_value("CRM Task", open_review_task, "status", "Done")

		if old_status != self.status and self.status == "Accepted":
			review_task = frappe.db.get_value(
				"CRM Task",
				{
					"reference_doctype": "CRM Lead",
					"reference_docname": self.lead,
					"task_type": "review_quote",
					"status": ["in", ["Todo", "In Progress"]],
				},
				"name",
			)
			if review_task:
				frappe.db.set_value("CRM Task", review_task, "status", "Done")

	def _guard_status_transition(self):
		"""Block Estimation Team (and any non-reviewer) from accepting or requesting revision.

		The form script hides the status dropdown for Estimation Team, but a direct
		frappe.client.set_value call would bypass that. This server-side gate mirrors
		the form script's canReview logic: lead_owner or REVIEWER_ROLES only.
		"""
		old_doc = self.get_doc_before_save()
		if old_doc is None or old_doc.status == self.status:
			return
		if self.status not in ("Accepted", "Revision Requested"):
			return

		user = frappe.session.user
		if user == "Administrator":
			return

		lead_owner = frappe.db.get_value("CRM Lead", self.lead, "lead_owner")
		if user == lead_owner:
			return

		user_roles = set(frappe.get_roles(user))
		if not (user_roles & REVIEWER_ROLES):
			frappe.throw(
				frappe._(
					"Only the Lead Owner or an authorized manager can Accept or Request Revision on a quote."
				),
				frappe.PermissionError,
			)

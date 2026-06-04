import frappe
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
	def validate(self):
		self._guard_status_transition()
		self._validate_mandatory_revision_notes()

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

	def _validate_mandatory_revision_notes(self):
		if self.status == "Revision Requested":
			if not (self.revision_notes or "").strip():
				frappe.throw(
					frappe._("Revision Notes are required when requesting a revision."),
					title=frappe._("Revision Notes Required"),
				)

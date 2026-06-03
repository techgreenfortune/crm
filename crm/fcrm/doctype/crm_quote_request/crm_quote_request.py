import frappe
from frappe.model.document import Document

from crm.permissions.role_config import TIER1_FULL_RW

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
	pass

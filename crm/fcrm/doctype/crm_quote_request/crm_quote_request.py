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
	- Everyone else: only QRs whose parent lead they own or are assigned to;
	  ASM/RSM additionally see QRs for leads owned by their downstream hierarchy.

	NOTE: this hook supersedes the "CRM Quote Request — Permission Query" Server
	Script (removed from server_script.json on 2026-05-25). The old script also
	granted access via a `tabCRM Task.assigned_to = user` clause; that branch was
	dropped intentionally — QR access now follows lead ownership / _assign only.
	The over-permissive task-assignment branch caused stale `review_quote` tasks
	to leak QR visibility to former lead-owners after a lead was reassigned.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & _QR_BYPASS_ROLES:
		return ""

	esc = frappe.db.escape
	# _assign is JSON like '["user@example.com"]'; the %"<user>"% pattern avoids
	# prefix collisions (ali@... matching alice@...).
	assign_like = esc('%"' + user + '"%')

	owners = {user}
	if roles & {"ASM", "RSM"}:
		from crm.overrides.crm_lead_permissions import downstream_users

		owners |= downstream_users(user)

	in_list = ", ".join(esc(u) for u in sorted(owners))

	return f"""EXISTS (
		SELECT 1 FROM `tabCRM Lead`
		WHERE `tabCRM Lead`.name = `tabCRM Quote Request`.lead
		AND (
			`tabCRM Lead`.lead_owner IN ({in_list})
			OR `tabCRM Lead`.lead_owner IS NULL
			OR `tabCRM Lead`.lead_owner = ''
			OR `tabCRM Lead`._assign LIKE {assign_like}
		)
	)"""


class CRMQuoteRequest(Document):
	pass

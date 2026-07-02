r"""Affiliate commission approval workflow.

Spec recap (see docs/affiliate-commission-approval.md if it exists):

- A "Sales Head" is the only role that can approve affiliate commissions.
- Sales user picks WHICH Sales Head to send the request to (avoiding the
  "first-to-act" race).  Any other Sales Head can still act if needed —
  the picker is a routing hint, not a strict assignee gate.
- Approval is the final gate before ``crm.api.projects.create_project_for_lead``
  is allowed to fire OpsGate.  C4 (Won) transition itself is unblocked.
- Editing ``custom_affiliate_commission_pct`` / ``custom_affiliate`` /
  ``custom_is_affiliate_lead`` AFTER approval resets the status back to
  empty — the sales user must re-submit.  Handled by the server script
  hooked on CRM Lead before_save (see crm/fixtures/server_script.json).

State machine::

    (empty) --[submit]-> Pending Approval --[approve]-> Approved
                                          \--[reject]-> Rejected --[edit]-> (empty)

Each transition writes a Comment to the lead's Activities timeline and
queues a Brevo email via ``crm.integrations.brevo.affiliate_emails``.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from crm.utils import sales_user_only

# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


@frappe.whitelist()
@sales_user_only
def get_sales_heads() -> list[dict]:
	"""Return active users carrying the ``Sales Head`` role.

	Powers the picker shown when a sales user clicks "Submit for Approval".
	Sorted alphabetically by full name.  An empty list means no Sales Head
	is configured — submission will fail with a clear error in that case.
	"""
	role_rows = frappe.db.get_all(
		"Has Role",
		filters={"role": "Sales Head", "parenttype": "User"},
		fields=["parent"],
	)
	if not role_rows:
		return []

	users = sorted({r["parent"] for r in role_rows})
	enabled_users = set(
		frappe.db.get_all(
			"User",
			filters={"enabled": 1, "name": ["in", users]},
			pluck="name",
		)
	)
	if not enabled_users:
		return []

	return frappe.get_all(
		"User",
		filters={"name": ["in", list(enabled_users)]},
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
	)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_sales_head(user: str | None = None) -> bool:
	"""Whether ``user`` (defaults to the session user) carries the Sales
	Head role.  System Manager also returns True — implicit super-approver."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	return bool(roles & {"Sales Head", "System Manager"})


def _can_act_on_approval(lead, user: str | None = None) -> bool:
	"""Whether ``user`` (defaults to session) is the *specific* approver for
	this lead — i.e. the Sales Head that the sales user picked when submitting,
	OR a super-approver (System Manager / Administrator).

	Other Sales Heads are NOT allowed — accountability stays with the picked
	one.  Use this instead of ``_is_sales_head`` for approve / reject gates.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if "System Manager" in frappe.get_roles(user):
		return True
	return lead.get("custom_affiliate_submitted_to") == user


def _validate_affiliate_lead(lead) -> None:
	"""Throw if the lead isn't a valid affiliate lead for approval actions."""
	if not lead.get("custom_is_affiliate_lead"):
		frappe.throw(_("This is not an affiliate lead — no approval needed."))
	if not lead.get("custom_affiliate"):
		frappe.throw(_("Pick an affiliate before submitting for approval."))
	commission = lead.get("custom_affiliate_commission_pct")
	if commission is None or commission <= 0:
		frappe.throw(_("Commission percentage must be greater than 0."))


def _audit_comment(lead_name: str, content: str) -> None:
	"""Pin an [AFFILIATE] comment to the lead's Activities timeline."""
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead_name,
				"content": content,
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Comment is audit-only; never block the workflow if it fails.
		frappe.log_error(
			title="Affiliate approval — audit comment failed",
			message=frappe.get_traceback(),
		)


def _close_pending_approval_task(lead_name: str) -> None:
	"""Close any open ``affiliate_approval`` task pointing at this lead."""
	open_task = frappe.db.get_value(
		"CRM Task",
		{
			"reference_doctype": "CRM Lead",
			"reference_docname": lead_name,
			"task_type": "affiliate_approval",
			"status": ["in", ["Todo", "In Progress"]],
		},
		"name",
	)
	if open_task:
		frappe.db.set_value("CRM Task", open_task, "status", "Done")


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------


@frappe.whitelist()
@sales_user_only
def submit_for_approval(lead_name: str, sales_head: str) -> dict:
	"""Sales user submits the affiliate commission to a chosen Sales Head.

	Side effects:
	- Sets ``custom_affiliate_approval_status = "Pending Approval"`` plus
	  submitted_to / submitted_by / submitted_at fields
	- Creates a ``CRM Task`` with ``task_type="affiliate_approval"``
	  assigned to ``sales_head`` for their queue
	- Enqueues a Brevo email to the chosen Sales Head
	- Pins an [AFFILIATE] Comment to the lead timeline
	"""
	from crm.integrations.brevo.affiliate_emails import fire_affiliate_email

	if not frappe.db.exists("CRM Lead", lead_name):
		frappe.throw(_("Lead {0} not found").format(lead_name), frappe.DoesNotExistError)

	if not sales_head or not frappe.db.exists("User", sales_head):
		frappe.throw(_("Pick a valid Sales Head to send the request to."))

	# Defense in depth — confirm the picked user actually has Sales Head role.
	if not _is_sales_head(sales_head):
		frappe.throw(_("{0} does not have the Sales Head role.").format(sales_head))

	lead = frappe.get_doc("CRM Lead", lead_name)
	_validate_affiliate_lead(lead)

	# Tentative value is required at submit time because the Sales Head reviews
	# commission % AND its rupee amount.  Without tentative_value the approval
	# email shows ₹0 commission, which defeats the point of the review.
	tentative_value = lead.get("custom_tentative_value")
	if tentative_value is None or float(tentative_value) <= 0:
		frappe.throw(
			_(
				"Fill the Tentative Value on the lead before submitting for approval — "
				"the Sales Head needs it to review the commission amount."
			)
		)

	now = now_datetime()
	lead.db_set(
		{
			"custom_affiliate_approval_status": "Pending Approval",
			"custom_affiliate_submitted_to": sales_head,
			"custom_affiliate_submitted_by": frappe.session.user,
			"custom_affiliate_submitted_at": now,
			"custom_affiliate_approval_remarks": None,
			"custom_affiliate_approved_by": None,
			"custom_affiliate_approved_at": None,
		},
		update_modified=True,
	)

	# Close any stale task from a previous round of submission.
	_close_pending_approval_task(lead_name)

	# Create the Sales Head's queue task.
	affiliate_name = (
		frappe.db.get_value("CRM Affiliate", lead.custom_affiliate, "affiliate_name") or lead.custom_affiliate
	)
	task = frappe.get_doc(
		{
			"doctype": "CRM Task",
			"task_type": "affiliate_approval",
			"title": f"Affiliate approval — {lead.lead_name or lead_name} ({lead.custom_affiliate_commission_pct}%)",
			"status": "Todo",
			"priority": "High",
			"assigned_to": sales_head,
			"reference_doctype": "CRM Lead",
			"reference_docname": lead_name,
			"description": (
				f"Sales user {frappe.session.user} has submitted the affiliate commission "
				f"for your approval.\n\n"
				f"Affiliate: {affiliate_name}\n"
				f"Commission: {lead.custom_affiliate_commission_pct}%\n\n"
				f"Open the lead and click Approve or Reject."
			),
		}
	).insert(ignore_permissions=True)

	_audit_comment(
		lead_name,
		f"<b>[AFFILIATE]</b> Commission submitted for approval — "
		f"Affiliate: <b>{affiliate_name}</b>, "
		f"Commission: <b>{lead.custom_affiliate_commission_pct}%</b>, "
		f"Submitted to: <b>{sales_head}</b>",
	)

	fire_affiliate_email("affiliate_approval_request", lead_name)

	return {"ok": True, "task": task.name, "submitted_to": sales_head}


@frappe.whitelist()
@sales_user_only
def approve_commission(lead_name: str, remarks: str | None = None) -> dict:
	"""Sales Head approves the affiliate commission."""
	from crm.integrations.brevo.affiliate_emails import fire_affiliate_email

	if not frappe.db.exists("CRM Lead", lead_name):
		frappe.throw(_("Lead {0} not found").format(lead_name), frappe.DoesNotExistError)

	lead = frappe.get_doc("CRM Lead", lead_name)
	_validate_affiliate_lead(lead)

	# Only the picked Sales Head (or System Manager / Admin) can approve.
	# Any other Sales Head viewing the lead does NOT see / cannot fire this.
	if not _can_act_on_approval(lead):
		frappe.throw(
			_(
				"Only the assigned Sales Head ({0}) can approve this commission. "
				"Ask them to act, or have a System Manager override."
			).format(lead.get("custom_affiliate_submitted_to") or _("not assigned")),
			frappe.PermissionError,
		)

	if lead.get("custom_affiliate_approval_status") != "Pending Approval":
		frappe.throw(
			_("Commission is not pending approval (current status: {0}).").format(
				lead.get("custom_affiliate_approval_status") or _("not submitted")
			)
		)

	now = now_datetime()
	lead.db_set(
		{
			"custom_affiliate_approval_status": "Approved",
			"custom_affiliate_approval_remarks": (remarks or "").strip() or None,
			"custom_affiliate_approved_by": frappe.session.user,
			"custom_affiliate_approved_at": now,
		},
		update_modified=True,
	)

	_close_pending_approval_task(lead_name)

	_audit_comment(
		lead_name,
		f"<b>[AFFILIATE]</b> Commission approved by <b>{frappe.session.user}</b> "
		f"({lead.custom_affiliate_commission_pct}%)"
		+ (f"<br>Remarks: {frappe.utils.escape_html(remarks)}" if remarks else ""),
	)

	fire_affiliate_email("affiliate_approval_granted", lead_name)

	return {"ok": True, "status": "Approved"}


_RESET_TRIGGER_FIELDS = (
	"custom_is_affiliate_lead",
	"custom_affiliate",
	"custom_affiliate_commission_pct",
)


def on_lead_before_save(doc, method=None):
	"""Reset affiliate approval to empty when the commission proposal changes.

	Wired via ``doc_events["CRM Lead"]["before_save"]`` in ``hooks.py``.

	Rule: if the lead previously had an approval status (Pending / Approved
	/ Rejected) and ANY of ``custom_is_affiliate_lead``,
	``custom_affiliate``, or ``custom_affiliate_commission_pct`` is being
	changed, wipe the approval state.  The sales user has to explicitly
	re-submit through ``submit_for_approval`` again.

	Also closes any open ``affiliate_approval`` task so the Sales Head's
	queue doesn't show stale items.

	Skipped entirely on insert — there's nothing to reset.
	"""
	old = doc.get_doc_before_save()
	if old is None:
		return  # insert — nothing to reset

	# Only act when there was an existing approval state to invalidate.
	old_status = old.get("custom_affiliate_approval_status")
	if not old_status:
		return

	changed = any(doc.get(f) != old.get(f) for f in _RESET_TRIGGER_FIELDS)
	if not changed:
		return

	# Wipe approval state.  We mutate doc in-place so the save persists the
	# cleared values along with the user's edit — no extra DB round-trip.
	doc.custom_affiliate_approval_status = None
	doc.custom_affiliate_approval_remarks = None
	doc.custom_affiliate_submitted_to = None
	doc.custom_affiliate_submitted_by = None
	doc.custom_affiliate_submitted_at = None
	doc.custom_affiliate_approved_by = None
	doc.custom_affiliate_approved_at = None

	# Cancel the Sales Head's queue task — if any was open.
	try:
		_close_pending_approval_task(doc.name)
	except Exception:
		# Best-effort — never block a lead save on cleanup.
		frappe.log_error(
			title="Affiliate approval — failed to close task on reset",
			message=frappe.get_traceback(),
		)

	# Audit trail.  Describe which trigger field changed for the activity log.
	deltas = []
	for f in _RESET_TRIGGER_FIELDS:
		if doc.get(f) != old.get(f):
			deltas.append(f"{f}: {old.get(f)!r} → {doc.get(f)!r}")
	_audit_comment(
		doc.name,
		"<b>[AFFILIATE]</b> Approval state cleared — commission proposal changed."
		f"<br>{'<br>'.join(deltas)}"
		f"<br>Sales user must re-submit for approval before the project can be created.",
	)


@frappe.whitelist()
@sales_user_only
def reject_commission(lead_name: str, remarks: str | None = None) -> dict:
	"""Sales Head rejects the affiliate commission.  Remarks are optional —
	if not supplied, a default placeholder is stored."""
	from crm.integrations.brevo.affiliate_emails import fire_affiliate_email

	if not frappe.db.exists("CRM Lead", lead_name):
		frappe.throw(_("Lead {0} not found").format(lead_name), frappe.DoesNotExistError)

	lead = frappe.get_doc("CRM Lead", lead_name)
	_validate_affiliate_lead(lead)

	# Only the picked Sales Head (or System Manager / Admin) can reject.
	if not _can_act_on_approval(lead):
		frappe.throw(
			_(
				"Only the assigned Sales Head ({0}) can reject this commission. "
				"Ask them to act, or have a System Manager override."
			).format(lead.get("custom_affiliate_submitted_to") or _("not assigned")),
			frappe.PermissionError,
		)

	if lead.get("custom_affiliate_approval_status") != "Pending Approval":
		frappe.throw(
			_("Commission is not pending approval (current status: {0}).").format(
				lead.get("custom_affiliate_approval_status") or _("not submitted")
			)
		)

	final_remarks = (remarks or "").strip() or _("No reason given")

	now = now_datetime()
	lead.db_set(
		{
			"custom_affiliate_approval_status": "Rejected",
			"custom_affiliate_approval_remarks": final_remarks,
			"custom_affiliate_approved_by": frappe.session.user,
			"custom_affiliate_approved_at": now,
		},
		update_modified=True,
	)

	_close_pending_approval_task(lead_name)

	_audit_comment(
		lead_name,
		f"<b>[AFFILIATE]</b> Commission rejected by <b>{frappe.session.user}</b> "
		f"({lead.custom_affiliate_commission_pct}%)<br>"
		f"Remarks: {frappe.utils.escape_html(final_remarks)}",
	)

	fire_affiliate_email("affiliate_approval_rejected", lead_name)

	return {"ok": True, "status": "Rejected"}

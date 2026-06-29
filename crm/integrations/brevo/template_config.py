"""Brevo template IDs for the Quote Request workflow.

Single source of truth for which Brevo transactional template renders each
event.  Change a value here → commit → ``bench restart`` to pick up.

The static estimation-team recipient address lives in ``site_config.json``
under the key ``estimation_email`` — keeping it out of code so it can be
changed without a deploy and varied per environment.

Leave a template ID as ``0`` (or remove the key) to disable that trigger — the
dispatcher in ``quote_emails.py`` treats falsy IDs as a soft no-op so partial
rollout is fine while templates are still being designed in Brevo.

Adding a new trigger
--------------------
1. Add an entry here, e.g. ``"quote_expired": 5``.
2. Add a ``_params_quote_expired`` builder + ``TRIGGERS`` entry in
   ``quote_emails.py`` that references ``"quote_expired"``.
3. Call ``_enqueue("quote_expired", qr_name)`` from wherever the event fires.
"""

# Brevo template IDs.  Find these in Brevo → Transactional → Email Templates.
# Set to 0 to disable a trigger.
BREVO_TEMPLATES: dict[str, int] = {
	# Quote Request workflow
	"quote_requested": 154,
	"quote_received": 155,
	"revision_requested": 156,
	"quote_accepted": 157,
	# Task workflow
	"task_reminder": 158,
	# Affiliate commission approval workflow
	"affiliate_approval_request": 160,  # TODO: design template in Brevo and fill in the ID
	"affiliate_approval_granted": 161,  # TODO: design template in Brevo and fill in the ID
	"affiliate_approval_rejected": 162,  # TODO: design template in Brevo and fill in the ID
}

# Static CC addresses merged into each trigger's recipient list at send time.
# Use this for fixed stakeholders (regional sales heads, compliance, etc.) who
# should be copied regardless of who the dynamic recipient resolver returns.
STATIC_CCS: dict[str, list[str]] = {
	"quote_received": ["salesao@indiframe.com"],
}

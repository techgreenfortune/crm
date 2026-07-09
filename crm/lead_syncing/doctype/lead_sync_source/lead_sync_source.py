# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from crm.lead_syncing.doctype.lead_sync_source.facebook import (
	PAID_LEAD_SOURCE,
	FacebookSyncSource,
	fetch_and_store_pages_from_facebook,
)


class LeadSyncSource(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_token: DF.Password
		background_sync_frequency: DF.Literal[
			"Every 5 Minutes", "Every 10 Minutes", "Every 15 Minutes", "Hourly", "Daily", "Monthly"
		]
		enabled: DF.Check
		facebook_lead_form: DF.Link | None
		facebook_page: DF.Link | None
		last_synced_at: DF.Datetime | None
		sub_source: DF.Link | None
		type: DF.Literal["Facebook"]
	# end: auto-generated types

	def validate(self):
		self.validate_same_fb_form_active()
		self.validate_sub_source_scope()

	def validate_sub_source_scope(self):
		"""The Vue Link's source=Paid filter is cosmetic only — enforce it server-side
		too, since sub_source can also be set via bulk edit / API / set_value."""
		if not self.sub_source:
			return

		sub_source_scope = frappe.db.get_value("CRM Sub Source", self.sub_source, "source")
		if sub_source_scope is None:
			frappe.throw(frappe._("Sub Source {0} does not exist.").format(self.sub_source))
		if sub_source_scope != PAID_LEAD_SOURCE:
			frappe.throw(
				frappe._("Sub Source {0} is not scoped to source {1}.").format(
					self.sub_source, PAID_LEAD_SOURCE
				)
			)

	def validate_same_fb_form_active(self):
		if not self.enabled:
			return

		if not self.facebook_lead_form:
			return

		already_active = frappe.db.exists(
			"Lead Sync Source",
			{"enabled": 1, "facebook_lead_form": self.facebook_lead_form, "name": ["!=", self.name]},
		)

		if already_active:
			frappe.throw(frappe._("A lead sync source is already enabled for this Facebook Lead Form!"))

	def before_insert(self):
		if self.type == "Facebook" and self.access_token:
			fetch_and_store_pages_from_facebook(self.access_token)
		# rest of the source types can be added here

	@frappe.whitelist()
	def sync_leads(self):
		if frappe.conf.developer_mode:
			self._sync_leads()
			return

		frappe.enqueue_doc(self.doctype, self.name, "_sync_leads", queue="long")

	def _sync_leads(self):
		if self.type == "Facebook" and self.access_token:
			if not self.facebook_lead_form:
				frappe.throw(frappe._("Please select a lead gen form before syncing!"))

			FacebookSyncSource(
				self.get_password("access_token"), self.facebook_lead_form, sub_source=self.sub_source
			).sync()

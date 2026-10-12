# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from crm.integrations.meta.audience import add_users_to_audience, remove_users_from_audience


class FailedMetaSyncLog(Document):
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		entity: DF.Literal["CRM Lead", "CRM Deal"]
		entity_name: DF.DynamicLink | None
		payload_row: DF.Code | None
		status: DF.Literal["Failure", "Retried"]
		sync_type: DF.Literal["Add", "Remove"]
		traceback: DF.Code | None

	@frappe.whitelist()
	def retry_sync(self):
		row = frappe.parse_json(self.payload_row)
		if self.sync_type == "Add":
			add_users_to_audience([row])
		else:
			remove_users_from_audience([row])

		self.status = "Retried"
		self.save()

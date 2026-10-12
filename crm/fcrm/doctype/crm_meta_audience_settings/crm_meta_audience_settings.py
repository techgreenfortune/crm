# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CRMMetaAudienceSettings(Document):
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_token: DF.Password | None
		audience_id: DF.Data | None
		enabled: DF.Check
		graph_api_version: DF.Data | None

	def on_update(self):
		frappe.clear_document_cache("CRM Meta Audience Settings", "CRM Meta Audience Settings")

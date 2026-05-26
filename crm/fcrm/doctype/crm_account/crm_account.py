# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMAccount(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_logo: DF.AttachImage | None
		account_name: DF.Data
		account_type: DF.Literal["Architect", "Builder", "Contractor", "Dealer"]
		address: DF.Link | None
		email: DF.Data | None
		industry: DF.Link | None
		mobile_no: DF.Data | None
		territory: DF.Link | None
		website: DF.Data | None
	# end: auto-generated types

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Account",
				"type": "Data",
				"key": "account_name",
				"width": "16rem",
			},
			{
				"label": "Account Type",
				"type": "Select",
				"key": "account_type",
				"width": "12rem",
			},
			{
				"label": "Mobile No",
				"type": "Data",
				"key": "mobile_no",
				"width": "12rem",
			},
			{
				"label": "Industry",
				"type": "Link",
				"key": "industry",
				"options": "CRM Industry",
				"width": "14rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "8rem",
			},
		]
		rows = [
			"name",
			"account_name",
			"account_logo",
			"account_type",
			"mobile_no",
			"industry",
			"modified",
		]
		return {"columns": columns, "rows": rows}

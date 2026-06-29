# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMAffiliate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		affiliate_name: DF.Data
		bank_account_number: DF.Data | None
		default_commission_pct: DF.Percent | None
		email: DF.Data | None
		gst_number: DF.Data | None
		mobile_no: DF.Data | None
		notes: DF.SmallText | None
		pan: DF.Data | None
		status: DF.Literal["Active", "Inactive"]
	# end: auto-generated types

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Affiliate",
				"type": "Data",
				"key": "affiliate_name",
				"width": "16rem",
			},
			{
				"label": "Status",
				"type": "Select",
				"key": "status",
				"width": "8rem",
			},
			{
				"label": "Email",
				"type": "Data",
				"key": "email",
				"width": "14rem",
			},
			{
				"label": "Mobile No",
				"type": "Data",
				"key": "mobile_no",
				"width": "10rem",
			},
			{
				"label": "Default Commission %",
				"type": "Percent",
				"key": "default_commission_pct",
				"width": "10rem",
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
			"affiliate_name",
			"status",
			"email",
			"mobile_no",
			"default_commission_pct",
			"modified",
		]
		return {"columns": columns, "rows": rows}

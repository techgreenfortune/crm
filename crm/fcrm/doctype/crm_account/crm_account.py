# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class CRMAccount(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_name: DF.Data
		account_type: DF.Literal["Architect", "Builder", "Contractor", "Dealer"]
		mobile_no: DF.Data | None
		email: DF.Data | None
	# end: auto-generated types

	pass

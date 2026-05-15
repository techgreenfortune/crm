# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMLeadEngagementStatus(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		color: DF.Literal[
			"black",
			"gray",
			"blue",
			"green",
			"red",
			"pink",
			"orange",
			"amber",
			"yellow",
			"cyan",
			"teal",
			"violet",
			"purple",
		]
		engagement_status: DF.Data
		position: DF.Int
	# end: auto-generated types

	pass

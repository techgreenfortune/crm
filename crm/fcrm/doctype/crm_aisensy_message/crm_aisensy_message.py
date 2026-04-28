import frappe
from frappe.model.document import Document


class CRMAISensyMessage(Document):
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		message_id: DF.Data | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		status: DF.Literal["Sent", "Failed"]
		template_name: DF.Data
		to: DF.Data
		variables: DF.SmallText | None

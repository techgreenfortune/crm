from frappe.model.document import Document


class CRMQuoteRevisionImage(Document):
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		image: DF.AttachImage
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		uploaded_by: DF.Link | None
		uploaded_on: DF.Datetime | None

	pass

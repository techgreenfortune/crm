import io

import frappe
from frappe.tests.utils import FrappeTestCase
from openpyxl import load_workbook

from crm.api.dashboard import download_c6_export
from crm.tests.factories import make_lead


class TestC6Export(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _lost(self, phone, entered_on=None, log_date=None):
		lead = make_lead(phone=phone, first_name=phone[-3:])
		values = {"status": "C6", "lost_reason": "Budget Constraints", "lost_notes": "too pricey"}
		if entered_on:
			values["custom_c6_entered_on"] = entered_on
		frappe.db.set_value("CRM Lead", lead.name, values, update_modified=False)
		if log_date:
			frappe.get_doc(
				{
					"doctype": "CRM Status Change Log",
					"parenttype": "CRM Lead",
					"parentfield": "status_change_log",
					"parent": lead.name,
					"from": "C6",
					"from_date": log_date,
				}
			).db_insert()
		return lead.name

	def _export(self, **kwargs):
		frappe.response.pop("filecontent", None)
		download_c6_export(**kwargs)
		ws = load_workbook(io.BytesIO(frappe.response["filecontent"])).active
		rows = list(ws.iter_rows(values_only=True))
		header = rows[0]
		return header, {r[header.index("Lead ID")]: dict(zip(header, r, strict=True)) for r in rows[1:]}

	def test_filters_c6_by_entry_date(self):
		stamped = self._lost("+919800000961", entered_on="2026-03-10 10:00:00")
		logged = self._lost("+919800000962", log_date="2026-03-12 09:00:00")
		outside = self._lost("+919800000963", entered_on="2026-01-01 10:00:00")
		open_lead = make_lead(phone="+919800000964").name

		header, rows = self._export(from_date="2026-03-01", to_date="2026-03-31")

		self.assertIn("C6 Entered On", header)
		self.assertIn(stamped, rows)
		self.assertIn(logged, rows)
		self.assertNotIn(outside, rows)
		self.assertNotIn(open_lead, rows)
		self.assertEqual(rows[stamped]["Lost Reason"], "Budget Constraints")
		self.assertEqual(rows[stamped]["Lost Notes"], "too pricey")
		self.assertEqual(str(rows[logged]["C6 Entered On"])[:10], "2026-03-12")
		self.assertTrue(frappe.response["filename"].startswith("c6_lost_export_all_"))

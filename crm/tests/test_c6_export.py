import io

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.nestedset import rebuild_tree
from openpyxl import load_workbook

from crm.api.dashboard import download_c6_export
from crm.tests.factories import make_hierarchy_node, make_lead, make_user


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

	def test_blocks_roles_outside_c6_export_roles(self):
		for profile in ("Sales Executive", "Calling Team", "Marketing"):
			email = f"c6-{profile.lower().replace(' ', '-')}@test.local"
			make_user(email=email, role_profile=profile, full_name=f"C6 {profile}")
			frappe.set_user(email)
			with self.subTest(profile=profile), self.assertRaises(frappe.PermissionError):
				download_c6_export()
			frappe.set_user("Administrator")

	def test_asm_scoped_to_own_and_picked_downstream(self):
		asm, se, outsider = "c6-asm@test.local", "c6-se@test.local", "c6-out@test.local"
		make_user(email=asm, role_profile="ASM", full_name="C6 ASM")
		make_user(email=se, role_profile="Sales Executive", full_name="C6 SE")
		make_user(email=outsider, role_profile="Sales Executive", full_name="C6 Out")
		make_hierarchy_node(user=asm, is_group=1)
		make_hierarchy_node(user=se, reports_to=asm)
		make_hierarchy_node(user=outsider)
		rebuild_tree("CRM Sales Hierarchy")

		leads = {}
		for owner, phone in ((asm, "+919800000971"), (se, "+919800000972"), (outsider, "+919800000973")):
			leads[owner] = self._lost(phone, entered_on="2026-03-10 10:00:00")
			frappe.db.set_value("CRM Lead", leads[owner], "lead_owner", owner, update_modified=False)

		frappe.set_user(asm)
		_, own = self._export()
		self.assertEqual(set(own), {leads[asm]})

		_, picked = self._export(owners=[se, outsider])
		self.assertEqual(set(picked), {leads[se]})

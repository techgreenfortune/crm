"""INDF-2009 — admin-configurable restricted fields (Quote Margin).

Hidden user: a Sales Coordinator (reads every lead and permlevel-1 QR fields, not
exempt). Rules are set on FCRM Settings and wiped after each test.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.permissions import field_visibility as fv
from crm.tests.factories import make_lead, make_user

HIDDEN_USER = "rf-coordinator@test.local"
OTHER_USER = "rf-coordinator2@test.local"
EXEMPT_USER = "rf-estimator@test.local"


def _set_rules(*rules):
	settings = frappe.get_doc("FCRM Settings")
	settings.set("restricted_field_rules", [])
	for rule in rules:
		settings.append("restricted_field_rules", rule)
	settings.save(ignore_permissions=True)


def _role_rule(role="Sales Coordinator"):
	return {"field_group": "Quote Margin", "applies_to": "Role", "role": role}


def _user_rule(user=HIDDEN_USER):
	return {"field_group": "Quote Margin", "applies_to": "User", "user": user}


class TestRestrictedFields(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(email=HIDDEN_USER, roles=["Sales Coordinator"])
		make_user(email=OTHER_USER, roles=["Sales Coordinator"])
		make_user(email=EXEMPT_USER, roles=["Sales Coordinator", "Estimation Team"])
		frappe.db.commit()

	def setUp(self):
		super().setUp()
		self._enqueue = patch("frappe.enqueue")
		self._enqueue.start()
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		self._enqueue.stop()
		frappe.db.rollback()
		frappe.clear_document_cache("FCRM Settings", "FCRM Settings")
		super().tearDown()

	def _lead_with_qr(self, phone):
		lead = make_lead(phone=phone)
		frappe.db.set_value("CRM Lead", lead.name, "custom_final_margin", 18)
		qr = frappe.new_doc("CRM Quote Request")
		qr.update({"lead": lead.name, "status": "Pending", "quote_margin": 18})
		qr.flags.ignore_mandatory = True
		qr.insert(ignore_permissions=True)
		return lead, qr

	# ---------- rule resolution ----------

	def test_no_rules_hides_nothing(self):
		_set_rules()
		self.assertEqual(fv.get_hidden_fields("CRM Lead", HIDDEN_USER), frozenset())

	def test_role_rule_hides_for_every_holder(self):
		_set_rules(_role_rule())
		for user in (HIDDEN_USER, OTHER_USER):
			self.assertEqual(fv.get_hidden_fields("CRM Quote Request", user), {"quote_margin"})
			self.assertEqual(fv.get_hidden_fields("CRM Lead", user), {"custom_final_margin"})

	def test_user_rule_hides_only_that_user(self):
		_set_rules(_user_rule())
		self.assertEqual(fv.get_hidden_fields("CRM Quote Request", HIDDEN_USER), {"quote_margin"})
		self.assertEqual(fv.get_hidden_fields("CRM Quote Request", OTHER_USER), frozenset())

	def test_exempt_role_and_administrator_beat_rules(self):
		_set_rules(_role_rule(), _user_rule(EXEMPT_USER))
		self.assertEqual(fv.get_hidden_groups(EXEMPT_USER), frozenset())
		self.assertEqual(fv.get_hidden_groups("Administrator"), frozenset())

	def test_unknown_group_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			_set_rules({"field_group": "Nope", "applies_to": "Role", "role": "Sales Coordinator"})

	def test_save_api_requires_system_manager(self):
		frappe.set_user(HIDDEN_USER)
		with self.assertRaises(frappe.PermissionError):
			fv.save_restricted_field_rules(json.dumps([_role_rule()]))

	def test_my_hidden_fields(self):
		_set_rules(_user_rule())
		frappe.set_user(HIDDEN_USER)
		self.assertEqual(
			fv.get_my_hidden_fields(),
			{"CRM Lead": ["custom_final_margin"], "CRM Quote Request": ["quote_margin"]},
		)

	# ---------- enforcement ----------

	def test_doc_load_strips_hidden_fields(self):
		from frappe.client import get

		_set_rules(_user_rule())
		lead, qr = self._lead_with_qr("+919870000001")
		frappe.set_user(HIDDEN_USER)
		# as_dict re-adds stripped float fields as 0 — the real value must not leak.
		self.assertFalse(get("CRM Lead", lead.name).get("custom_final_margin"))
		self.assertFalse(get("CRM Quote Request", qr.name).get("quote_margin"))
		frappe.set_user(OTHER_USER)
		self.assertEqual(get("CRM Quote Request", qr.name)["quote_margin"], 18)

	def test_whole_doc_save_keeps_hidden_value(self):
		_set_rules(_user_rule())
		lead, _qr = self._lead_with_qr("+919870000002")
		frappe.set_user(HIDDEN_USER)
		for echoed in (None, 0):
			doc = frappe.get_doc("CRM Lead", lead.name)
			doc.custom_final_margin = echoed
			doc.first_name = "Renamed"
			doc.save()
			self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "custom_final_margin"), 18)

	def test_quote_list_and_revisions_strip_margin(self):
		from crm.api.activities import get_quote_revisions
		from crm.api.quotes import list_lead_quote_requests

		_set_rules(_user_rule())
		lead, _qr = self._lead_with_qr("+919870000003")
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
				"content": "[AUTOMATION] Quote uploaded — Value: 1000, Margin: 18%",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(HIDDEN_USER)
		self.assertIsNone(list_lead_quote_requests(lead.name)[0].get("quote_margin"))
		revisions = get_quote_revisions(lead.name)
		self.assertTrue(revisions)
		self.assertIsNone(revisions[0]["margin"])

	def test_get_data_strips_columns_and_values(self):
		from crm.api.doc import get_data

		_set_rules(_user_rule())
		lead, _qr = self._lead_with_qr("+919870000004")
		frappe.set_user(HIDDEN_USER)
		data = get_data(
			"CRM Lead",
			{"name": lead.name},
			"modified desc",
			columns=[{"label": "Margin", "key": "custom_final_margin"}],
			rows=["name", "custom_final_margin"],
		)
		self.assertNotIn("custom_final_margin", [c.get("key") for c in data["columns"]])
		self.assertNotIn("custom_final_margin", data["rows"])
		for row in data["data"]:
			self.assertIsNone(row.get("custom_final_margin"))

	def test_activity_text_masked(self):
		_set_rules(_user_rule())
		frappe.set_user(HIDDEN_USER)
		self.assertEqual(
			fv.mask_text("[AUTOMATION] Quote uploaded — Value: 1000, Margin: 18.5%"),
			"[AUTOMATION] Quote uploaded — Value: 1000",
		)
		data = fv.strip_version_changes(
			"CRM Lead", {"changed": [["custom_final_margin", 1, 2], ["first_name", "a", "b"]]}
		)
		self.assertEqual(data["changed"], [["first_name", "a", "b"]])

	def test_layout_marks_field_hidden(self):
		from crm.fcrm.doctype.crm_fields_layout.crm_fields_layout import handle_perm_level_restrictions

		_set_rules(_user_rule())
		frappe.set_user(HIDDEN_USER)
		field = frappe._dict(fieldname="quote_margin", hidden=0)
		handle_perm_level_restrictions(field, "CRM Quote Request")
		self.assertEqual(field.hidden, 1)

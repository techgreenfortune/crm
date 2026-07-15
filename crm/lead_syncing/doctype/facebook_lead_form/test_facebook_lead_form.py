# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestFacebookLeadForm(FrappeTestCase):
	"""
	Integration tests for FacebookLeadForm.
	Use this class for testing interactions between multiple components.
	"""

	def setUp(self):
		super().setUp()
		self.page = frappe.get_doc(
			{
				"doctype": "Facebook Page",
				"id": "page-mandatory-check-1",
				"page_name": "Mandatory Check Page",
				"category": "Business",
				"access_token": "page-token",
				"account_id": "acct-mandatory-check-1",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_new_form_with_no_first_name_mapping_raises(self):
		"""check_mandatory_crm_fields_mapped previously no-opped on creation
		(is_new() always True during insert's validate()) — the exact
		production regression this feature is meant to prevent could still
		slip through unnoticed. Must now throw at creation time."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Facebook Lead Form",
					"id": "form-mandatory-check-1",
					"page": self.page.name,
					"form_name": "No Name Mapping Form",
					"questions": [{"key": "your_full_name_here", "mapped_to_crm_field": None}],
				}
			).insert(ignore_permissions=True)

	def test_new_form_with_first_name_mapped_saves(self):
		doc = frappe.get_doc(
			{
				"doctype": "Facebook Lead Form",
				"id": "form-mandatory-check-2",
				"page": self.page.name,
				"form_name": "Valid Mapping Form",
				"questions": [{"key": "full_name", "mapped_to_crm_field": "first_name"}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.name, "form-mandatory-check-2")

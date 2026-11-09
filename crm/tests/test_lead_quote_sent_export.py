"""Quotes sent today, filtered + exported from the Leads list (management view).

Drives the real QR flow (upload -> revision -> re-upload), then filters and
exports CRM Lead exactly the way the Leads list "Export" button does
(frappe.desk.reportview.export_query with the list's fields + filters).
"""

import io
import json
from unittest.mock import patch

import frappe
from frappe.desk.reportview import export_query
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime
from openpyxl import load_workbook

from crm.patches.v1_0.backfill_lead_last_quote_sent import execute as backfill
from crm.tests.factories import make_lead, make_user

# Frappe skips Version rows in tests by default; prod writes them and the backfill reads them.
QUOTE = {"quote_margin": 20, "quote_sq_ft": 100, "total_quantity": 4, "quote_file": "/files/q.pdf"}
FIELDS = [
	"name",
	"custom_quotation_sent_on",
	"custom_last_quote_type",
	"custom_final_price",
	"custom_final_margin",
]
TODAY = {"custom_quotation_sent_on": ["timespan", "today"]}


def _silent_enqueue(*_args, **_kwargs):
	pass


class TestLeadQuoteSentExport(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._patches = [
			patch("frappe.enqueue", side_effect=_silent_enqueue),
			patch("frappe.utils.safe_exec.safe_enqueue", side_effect=_silent_enqueue),
		]
		for p in self._patches:
			p.start()

	def tearDown(self):
		for p in self._patches:
			p.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _new_qr(self, lead, **extra):
		qr = frappe.new_doc("CRM Quote Request")
		qr.update({"lead": lead, "status": "Pending", **extra})
		qr.flags.ignore_mandatory = True
		qr.insert(ignore_permissions=True)
		return qr

	def _upload(self, qr_name, value, number):
		"""Estimation attaches the quote: Pending -> Quote Received via auto-promote."""
		qr = frappe.get_doc("CRM Quote Request", qr_name)
		qr.update({**QUOTE, "quote_value": value, "quote_number": number})
		qr.save(ignore_permissions=True, ignore_version=False)
		self.assertEqual(qr.status, "Quote Received")
		return qr

	def _lead(self, name):
		return frappe.db.get_value("CRM Lead", name, FIELDS[1:], as_dict=True)

	def _scenario(self):
		# A: first quote, then a revision round.
		a = make_lead(phone="+919800000891", first_name="Alpha").name
		qr1 = self._upload(self._new_qr(a).name, 100000, "LQS-1")
		self.assertEqual(self._lead(a).custom_last_quote_type, "New")
		qr1.reload()
		qr1.status, qr1.notes = "Revision Requested", "Drop price"
		qr1.save(ignore_permissions=True, ignore_version=False)
		qr2 = frappe.get_value("CRM Quote Request", {"lead": a, "status": "Pending"})
		self._upload(qr2, 90000, "LQS-2")

		# B: quote imported straight in as Quote Received.
		b = make_lead(phone="+919800000892", first_name="Bravo").name
		self._new_qr(b, status="Quote Received", quote_value=50000, quote_number="LQS-3", **QUOTE)

		# C: quote accepted earlier, moved back by a manager today -> not a send.
		c = make_lead(phone="+919800000893", first_name="Charlie").name
		qr_c = self._new_qr(c, status="Accepted", quote_value=70000, quote_number="LQS-4", **QUOTE)
		frappe.db.set_value(
			"CRM Quote Request", qr_c.name, "creation", add_days(now_datetime(), -1), update_modified=False
		)
		qr_c.reload()
		qr_c.status = "Quote Received"
		qr_c.save(ignore_permissions=True, ignore_version=False)
		return a, b, c

	def _expected(self, a, b):
		return {a: ("Revised", 90000, 20), b: ("New", 50000, 20)}

	def _as_management(self):
		make_user(email="lqs-mgmt@example.com", roles=["Management"])
		frappe.set_user("lqs-mgmt@example.com")

	def test_leads_filter_shows_new_and_revised_sent_today(self):
		a, b, c = self._scenario()
		self._as_management()
		rows = frappe.get_list("CRM Lead", filters=TODAY, fields=FIELDS)
		got = {
			r.name: (r.custom_last_quote_type, r.custom_final_price, r.custom_final_margin)
			for r in rows
			if r.name in (a, b, c)
		}
		self.assertEqual(got, self._expected(a, b))

		revised = frappe.get_list(
			"CRM Lead", filters={**TODAY, "custom_last_quote_type": "Revised"}, pluck="name"
		)
		self.assertIn(a, revised)
		self.assertNotIn(b, revised)

	def test_management_exports_filtered_leads_to_excel(self):
		a, b, c = self._scenario()
		self._as_management()
		frappe.local.form_dict = frappe._dict(
			doctype="CRM Lead",
			file_format_type="Excel",
			title="CRM Lead",
			fields=json.dumps(FIELDS),
			filters=json.dumps(TODAY),
			order_by="modified desc",
			start=0,
			view="Report",
		)
		export_query()
		self.assertEqual(frappe.response["type"], "binary")

		sheet = load_workbook(io.BytesIO(frappe.response["filecontent"])).active
		header, *data = list(sheet.values)
		col = {label: i for i, label in enumerate(header)}
		got = {
			row[col["ID"]]: (
				row[col["Last Quote Type"]],
				row[col["Latest Quoted Price"]],
				row[col["Latest Quoted Margin"]],
			)
			for row in data
			if row[col["ID"]] in (a, b, c)
		}
		self.assertIn("Quotation Sent On", col)
		self.assertEqual(got, self._expected(a, b))

	def test_backfill_rebuilds_from_quote_history(self):
		a, b, c = self._scenario()
		before = {n: self._lead(n) for n in (a, b)}
		frappe.db.sql(
			"UPDATE `tabCRM Lead` SET custom_quotation_sent_on = NULL, custom_last_quote_type = NULL"
			" WHERE name IN %s",
			[(a, b, c)],
		)
		backfill()
		for n in (a, b):
			after = self._lead(n)
			self.assertEqual(after.custom_last_quote_type, before[n].custom_last_quote_type)
			self.assertEqual(after.custom_quotation_sent_on.date(), before[n].custom_quotation_sent_on.date())
		# C's only "Quote Received" change is the manager undo; it was imported as Accepted.
		self.assertIsNone(self._lead(c).custom_last_quote_type)

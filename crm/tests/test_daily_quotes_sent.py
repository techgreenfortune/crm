from unittest.mock import patch

import frappe
from frappe.desk.query_report import export_query, run
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, today

from crm.tests.factories import make_lead, make_user

REPORT = "Daily Quotes Sent"
QUOTE = {"quote_margin": 20, "quote_sq_ft": 100, "total_quantity": 4, "quote_file": "/files/q.pdf"}


def _silent_enqueue(*_args, **_kwargs):
	pass


class TestDailyQuotesSent(FrappeTestCase):
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

	def _upload(self, qr_name, value, number):
		"""Estimation uploads the quote: Pending -> Quote Received via auto-promote."""
		qr = frappe.get_doc("CRM Quote Request", qr_name)
		qr.update({**QUOTE, "quote_value": value, "quote_number": number})
		qr.save(ignore_permissions=True)
		self.assertEqual(qr.status, "Quote Received")
		return qr

	def _new_qr(self, lead, **extra):
		qr = frappe.new_doc("CRM Quote Request")
		qr.update({"lead": lead, "status": "Pending", **extra})
		qr.flags.ignore_mandatory = True
		qr.insert(ignore_permissions=True)
		return qr

	def _scenario(self):
		# Lead A: first quote, then a revision round with a new value.
		a = make_lead(phone="+919800000881", first_name="Alpha")
		qr1 = self._upload(self._new_qr(a.name).name, 100000, "DQS-1")
		qr1.reload()
		qr1.status, qr1.notes = "Revision Requested", "Drop price"
		qr1.save(ignore_permissions=True)
		qr2 = frappe.get_value("CRM Quote Request", {"lead": a.name, "status": "Pending"})
		self._upload(qr2, 90000, "DQS-2")

		# Lead B: quote imported straight in as Quote Received (no status-change Version).
		b = make_lead(phone="+919800000882", first_name="Bravo")
		self._new_qr(b.name, status="Quote Received", quote_value=50000, quote_number="DQS-3", **QUOTE)

		# Lead C: quote accepted yesterday, moved back today by a manager -> not a send.
		c = make_lead(phone="+919800000883", first_name="Charlie")
		qr_c = self._new_qr(c.name, status="Accepted", quote_value=70000, quote_number="DQS-4", **QUOTE)
		yesterday = add_days(now_datetime(), -1)
		frappe.db.set_value("CRM Quote Request", qr_c.name, "creation", yesterday, update_modified=False)
		qr_c.reload()
		qr_c.status = "Quote Received"
		qr_c.save(ignore_permissions=True)
		return qr1.name, qr2

	def test_new_and_revised_quotes_sent_today(self):
		qr1, qr2 = self._scenario()
		result = run(REPORT, {"from_date": today(), "to_date": today()})["result"]
		rows = [
			r
			for r in result
			if isinstance(r, dict) and str(r.get("quote_number", "")).startswith("TRA-QT-DQS")
		]

		got = {
			r["quote_number"]: (r["quote_type"], r["round"], r["quote_value"], r["quote_margin"])
			for r in rows
		}
		self.assertEqual(
			got,
			{
				"TRA-QT-DQS-1": ("New", "R1", 100000, 20),
				"TRA-QT-DQS-3": ("New", "R1", 50000, 20),
				"TRA-QT-DQS-2": ("Revised", "R2", 90000, 20),
			},
		)
		# New rows come before Revised rows.
		self.assertEqual([r["quote_type"] for r in rows], ["New", "New", "Revised"])

		# Nothing leaks into yesterday's report.
		y = add_days(today(), -1)
		old = run(REPORT, {"from_date": y, "to_date": y})["result"]
		self.assertFalse([r for r in old if isinstance(r, dict) and r.get("name") in (qr1, qr2)])

	def test_management_can_export_xlsx(self):
		self._scenario()
		make_user(email="dqs-mgmt@example.com", roles=["Management"])
		frappe.set_user("dqs-mgmt@example.com")
		frappe.local.form_dict = frappe._dict(
			report_name=REPORT,
			file_format_type="Excel",
			filters=frappe.as_json({"from_date": today(), "to_date": today()}),
			visible_idx="[]",
		)
		export_query()
		self.assertEqual(frappe.response["type"], "binary")
		self.assertTrue(frappe.response["filename"].endswith(".xlsx"))
		self.assertGreater(len(frappe.response["filecontent"]), 0)

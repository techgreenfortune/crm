import ast
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

import crm.install
from crm.install import SEED_DATA_DIR, SEED_FILES, SEED_LEDGER_KEY, _seed_key, seed_master_data


def _all_seed_records():
	records = []
	for fname in SEED_FILES:
		with open(os.path.join(SEED_DATA_DIR, fname)) as f:
			records += json.load(f)
	return records


def _ledger():
	return set(json.loads(frappe.defaults.get_global_default(SEED_LEDGER_KEY) or "[]"))


class TestSeedMasterData(FrappeTestCase):
	def setUp(self):
		self.records = _all_seed_records()
		# Last record of each file: nothing in the seed links to it.
		self.targets = [
			[r for r in self.records if r["doctype"] == doctype][-1]
			for doctype in dict.fromkeys(r["doctype"] for r in self.records)
		]

	def tearDown(self):
		frappe.defaults._clear_cache("__default")

	def _set_ledger(self, keys):
		frappe.defaults.set_global_default(SEED_LEDGER_KEY, json.dumps(sorted(keys)))

	def test_new_seed_record_inserted_once(self):
		new_keys = {_seed_key(r) for r in self.targets}
		self._set_ledger({_seed_key(r) for r in self.records} - new_keys)
		for r in self.targets:
			frappe.db.delete(r["doctype"], r["name"])

		seed_master_data()
		for r in self.targets:
			self.assertTrue(frappe.db.exists(r["doctype"], r["name"]), r["name"])

		for r in self.targets:
			frappe.db.delete(r["doctype"], r["name"])
		seed_master_data()
		for r in self.targets:
			self.assertFalse(frappe.db.exists(r["doctype"], r["name"]), f"{r['name']} resurrected")

	def test_existing_site_first_run_only_records_ledger(self):
		frappe.defaults.clear_default(SEED_LEDGER_KEY, parent="__default")
		for r in self.targets:
			frappe.db.delete(r["doctype"], r["name"])

		seed_master_data()

		for r in self.targets:
			self.assertFalse(frappe.db.exists(r["doctype"], r["name"]), r["name"])
		self.assertEqual(_ledger(), {_seed_key(r) for r in self.records})


class TestSeedWiring(FrappeTestCase):
	def test_runs_on_migrate(self):
		self.assertIn("crm.install.seed_master_data", frappe.get_hooks("after_migrate", app_name="crm"))

	def test_runs_on_install(self):
		tree = ast.parse(inspect.getsource(crm.install.after_install))
		called = {
			node.func.id
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
		}
		self.assertIn("seed_master_data", called)

	def test_seeded_doctypes_are_not_fixtures(self):
		# A fixture is force-imported on migrate, which would undo the seed ledger's guarantees.
		fixtures = {
			entry if isinstance(entry, str) else entry.get("doctype") or entry.get("dt")
			for entry in frappe.get_hooks("fixtures", app_name="crm")
		}
		self.assertFalse({r["doctype"] for r in _all_seed_records()} & fixtures)

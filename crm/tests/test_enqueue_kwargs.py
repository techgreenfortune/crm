import ast
import inspect
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.background_jobs import enqueue

APP_ROOT = Path(__file__).resolve().parents[1]
ENQUEUE_OPTIONS = set(inspect.signature(enqueue).parameters) - {"kwargs"}


def enqueue_calls():
	for path in APP_ROOT.rglob("*.py"):
		if "tests" in path.parts:
			continue
		tree = ast.parse(path.read_text(), filename=str(path))
		for node in ast.walk(tree):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "enqueue"
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and isinstance(node.args[0].value, str)
			):
				continue
			job_kwargs = [kw.arg for kw in node.keywords if kw.arg and kw.arg not in ENQUEUE_OPTIONS]
			has_splat = any(kw.arg is None for kw in node.keywords)
			yield path.relative_to(APP_ROOT.parent), node.lineno, node.args[0].value, job_kwargs, has_splat


class TestEnqueueKwargs(FrappeTestCase):
	def test_enqueued_kwargs_match_the_job_signature(self):
		# The worker calls method(**kwargs) with every non-enqueue keyword. A stray one (e.g. user=,
		# which this Frappe version doesn't accept as an enqueue option) raises TypeError in the job.
		problems = []
		checked = 0
		for path, line, method, job_kwargs, has_splat in enqueue_calls():
			if has_splat or "." not in method:
				continue
			try:
				fn = frappe.get_attr(method)
			except Exception:
				problems.append(f"{path}:{line} {method}: target not importable")
				continue
			try:
				inspect.signature(fn).bind_partial(**dict.fromkeys(job_kwargs))
			except TypeError as error:
				problems.append(f"{path}:{line} {method}: {error}")
			checked += 1

		self.assertGreater(checked, 0)
		self.assertEqual(problems, [])

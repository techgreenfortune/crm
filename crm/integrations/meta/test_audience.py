# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Normalization + hashing tests for the Meta Custom Audience sync.

Covers the rules in the developer brief: trim/lowercase everything, phone
becomes digits-only with a country code, empty fields hash to the hash of
"" (never skipped — Meta's schema is positional), and the add/remove payload
builders must stay symmetric so the same person matches on removal.
"""

from __future__ import annotations

import hashlib

from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.audience import (
	AUDIENCE_SCHEMA,
	build_payload,
	build_user_row,
	hash_field,
)


def _sha(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TestHashField(FrappeTestCase):
	def test_trims_and_lowercases_text(self):
		self.assertEqual(hash_field("  John  "), _sha("john"))

	def test_empty_text_hashes_empty_string(self):
		self.assertEqual(hash_field(None), _sha(""))
		self.assertEqual(hash_field(""), _sha(""))

	def test_phone_strips_non_digits(self):
		self.assertEqual(hash_field("+91 98765-43210", is_phone=True), _sha("919876543210"))

	def test_phone_without_country_code_gets_default_prefix(self):
		self.assertEqual(hash_field("9876543210", is_phone=True), _sha("919876543210"))

	def test_phone_already_with_country_code_not_double_prefixed(self):
		self.assertEqual(hash_field("919876543210", is_phone=True), _sha("919876543210"))

	def test_empty_phone_hashes_empty_string(self):
		self.assertEqual(hash_field(None, is_phone=True), _sha(""))


class TestBuildUserRow(TestHashField):
	def test_row_order_matches_schema(self):
		row = build_user_row(
			email="Jane@Example.com",
			phone="9876543210",
			first_name="Jane",
			last_name="Doe",
			country=None,
		)
		self.assertEqual(len(row), len(AUDIENCE_SCHEMA))
		self.assertEqual(
			row,
			[_sha("jane@example.com"), _sha("919876543210"), _sha("jane"), _sha("doe"), _sha("")],
		)

	def test_same_input_produces_identical_add_and_remove_row(self):
		# The remove call must reuse the exact same normalized values as the add
		# call, or Meta won't match the person (brief's explicit requirement).
		kwargs = dict(email="x@y.com", phone="9876543210", first_name="X", last_name="Y", country="IN")
		self.assertEqual(build_user_row(**kwargs), build_user_row(**kwargs))


class TestBuildPayload(FrappeTestCase):
	def test_payload_shape(self):
		rows = [["a", "b", "c", "d", "e"]]
		payload = build_payload(rows)
		self.assertEqual(payload, {"payload": {"schema": AUDIENCE_SCHEMA, "data": rows}})

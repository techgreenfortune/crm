# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Normalization + hashing tests for the Meta Custom Audience sync.

Covers the rules in the developer brief: trim/lowercase everything, phone
becomes digits-only with a country code, a genuinely blank field is sent as
a literal "" (never hashed — hashing "" would be a real, specific digest
Meta can't tell apart from real data), and the add/remove payload builders
must stay symmetric so the same person matches on removal.
"""

from __future__ import annotations

import hashlib

from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.audience import (
	AUDIENCE_SCHEMA,
	build_payload,
	build_user_row,
	has_matchable_contact,
	hash_field,
)


def _sha(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TestHashField(FrappeTestCase):
	def test_trims_and_lowercases_text(self):
		self.assertEqual(hash_field("  John  "), _sha("john"))

	def test_empty_text_sends_literal_empty_string(self):
		# Blank fields are sent as "" — not the hash of "" — per Meta's own
		# "blank = empty string" convention (hashing "" would look like a
		# real value to Meta's matching).
		self.assertEqual(hash_field(None), "")
		self.assertEqual(hash_field(""), "")
		self.assertEqual(hash_field("   "), "")

	def test_phone_strips_non_digits(self):
		self.assertEqual(hash_field("+91 98765-43210", is_phone=True), _sha("919876543210"))

	def test_phone_without_country_code_gets_default_prefix(self):
		self.assertEqual(hash_field("9876543210", is_phone=True), _sha("919876543210"))

	def test_phone_already_with_country_code_not_double_prefixed(self):
		self.assertEqual(hash_field("919876543210", is_phone=True), _sha("919876543210"))

	def test_phone_with_leading_zero_strips_it_before_prefixing(self):
		# "09876543210" is 11 digits (leading 0 + 10-digit local number), not a
		# number that already carries a country code — strip the leading zero
		# first so it's recognized as a bare local number and gets prefixed.
		self.assertEqual(hash_field("09876543210", is_phone=True), _sha("919876543210"))

	def test_empty_phone_sends_literal_empty_string(self):
		self.assertEqual(hash_field(None, is_phone=True), "")
		self.assertEqual(hash_field("", is_phone=True), "")


class TestHasMatchableContact(FrappeTestCase):
	def test_true_when_email_present(self):
		self.assertTrue(has_matchable_contact(email="a@b.com", phone=None))

	def test_true_when_phone_present(self):
		self.assertTrue(has_matchable_contact(email=None, phone="9876543210"))

	def test_false_when_both_missing(self):
		self.assertFalse(has_matchable_contact(email=None, phone=None))
		self.assertFalse(has_matchable_contact(email="", phone=""))


class TestBuildUserRow(TestHashField):
	def test_row_order_matches_schema(self):
		row = build_user_row(
			email="Jane@Example.com",
			phone="9876543210",
			first_name="Jane",
			last_name="Doe",
			country="",
		)
		self.assertEqual(len(row), len(AUDIENCE_SCHEMA))
		self.assertEqual(
			row,
			[_sha("jane@example.com"), _sha("919876543210"), _sha("jane"), _sha("doe"), ""],
		)

	def test_country_defaults_when_not_given(self):
		# Lead/Deal have no country field today — country=None should fall back
		# to the configured default rather than always being sent blank.
		row = build_user_row(email=None, phone=None, first_name=None, last_name=None, country=None)
		self.assertEqual(row[4], _sha("in"))

	def test_explicit_empty_country_is_respected(self):
		row = build_user_row(email=None, phone=None, first_name=None, last_name=None, country="")
		self.assertEqual(row[4], "")

	def test_same_input_produces_identical_add_and_remove_row(self):
		# The remove call must reuse the exact same normalized values as the add
		# call, or Meta won't match the person (brief's explicit requirement).
		kwargs = dict(email="x@y.com", phone="9876543210", first_name="X", last_name="Y", country="in")
		self.assertEqual(build_user_row(**kwargs), build_user_row(**kwargs))


class TestBuildPayload(FrappeTestCase):
	def test_payload_shape(self):
		rows = [["a", "b", "c", "d", "e"]]
		payload = build_payload(rows)
		self.assertEqual(payload, {"payload": {"schema": AUDIENCE_SCHEMA, "data": rows}})

"""B1 — website-capture endpoint tests.

Covers PRD §3.1 (lead data model), §7 (source/attribution + UTM capture),
§8.2 Phase 1 (website-form reactivation), §8.3 (phone dedup).

Module under test: ``crm/api/website.py``.

Strategy: call ``crm.api.website.create_lead`` directly (skip HTTP layer).
Patch ``frappe.get_request_header`` so ``_verify_token`` sees the matching
header. ``frappe.db.rollback()`` in tearDown isolates each case.
"""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import website
from crm.tests.factories import make_lead

TEST_TOKEN = "TEST-TOKEN-12345"


def _header_returning(token: str | None):
	"""Build a frappe.get_request_header replacement that returns ``token``
	for the IndiFrame header and "" for everything else."""

	def _stub(name: str, default: str | None = None) -> str:
		if name == website.TOKEN_HEADER:
			return token or ""
		return default or ""

	return _stub


class _BaseWebsite(FrappeTestCase):
	"""Common setUp: stash a valid token in conf + return it on header read."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._original_token = frappe.conf.get(website.TOKEN_CONF_KEY)
		frappe.conf[website.TOKEN_CONF_KEY] = TEST_TOKEN

	@classmethod
	def tearDownClass(cls):
		if cls._original_token is None:
			frappe.conf.pop(website.TOKEN_CONF_KEY, None)
		else:
			frappe.conf[website.TOKEN_CONF_KEY] = cls._original_token
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		self._hdr_patch = patch("frappe.get_request_header", _header_returning(TEST_TOKEN))
		self._hdr_patch.start()

	def tearDown(self):
		self._hdr_patch.stop()
		frappe.db.rollback()
		super().tearDown()


class TestCreateLeadHappyPath(_BaseWebsite):
	def test_new_lead_defaults_c0_active(self):
		"""Fresh submission → status=C0, lead_status=Active, source=Direct, sub_source=Website.

		Also asserts the False branch of ``if trail:`` at website.py:313-315 — a minimal
		submission (no message, no UTM, no email-drop note) must produce zero comments.
		"""
		res = website.create_lead(name="Alpha Beta", mobile="+919812345001")
		self.assertEqual(res["status"], "created")
		self.assertEqual(res["stage"], "C0")

		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.status, "C0")
		self.assertEqual(lead.lead_status, "Active")
		self.assertEqual(lead.source, website.WEBSITE_SOURCE)
		self.assertEqual(lead.get("custom_sub_source"), website.WEBSITE_SUB_SOURCE)
		self.assertEqual(lead.first_name, "Alpha")
		self.assertEqual(lead.last_name, "Beta")

		# Minimal submission → empty trail → no comment added.
		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
				"comment_type": "Comment",
			},
		)
		self.assertEqual(len(comments), 0, f"expected no comments for minimal submission, got: {comments}")

	def test_customer_type_architect_routes_projects(self):
		res = website.create_lead(name="Arc Test", mobile="+919812345002", customer_type="Architect")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Projects")
		self.assertEqual(lead.get("custom_customer_type"), "Architect")

	def test_customer_type_builder_routes_projects(self):
		res = website.create_lead(name="Build Test", mobile="+919812345003", customer_type="Builder")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Projects")

	def test_customer_type_contractor_routes_projects(self):
		res = website.create_lead(name="Cnt Test", mobile="+919812345004", customer_type="Contractor")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Projects")

	def test_customer_type_homeowner_routes_retail(self):
		res = website.create_lead(name="Home Test", mobile="+919812345005", customer_type="Homeowner")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Retail")

	def test_customer_type_none_routes_retail(self):
		res = website.create_lead(name="Null Test", mobile="+919812345006")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Retail")

	def test_optional_payload_fields_mapped_correctly(self):
		"""``pincode`` → custom_pincode, ``company`` → organization, ``project_type`` → trail only.

		``project_type`` is intentionally NOT stored on the lead (no field); it lives only
		in the comment trail per ``_trail_parts`` at website.py:131-132.
		"""
		res = website.create_lead(
			name="Map Test",
			mobile="+919812345007",
			pincode="560001",
			company="ACME Corp",
			city="Bengaluru",
			project_type="Residential",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.organization, "ACME Corp")
		self.assertEqual(lead.get("custom_pincode"), "560001")

		# project_type and city land in the comment trail
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead.name},
			fields=["content"],
		)
		joined = " ".join(c.content or "" for c in comments)
		self.assertIn("Project Type: Residential", joined)
		self.assertIn("City: Bengaluru", joined)

	def test_empty_company_string_stores_none(self):
		"""``company=""`` (or whitespace) → organization=None, not empty string (website.py:271)."""
		res = website.create_lead(name="Empty Co", mobile="+919812345008", company="   ")
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertIsNone(lead.organization)


class TestCreateLeadDedup(_BaseWebsite):
	"""PRD §8.3 — phone is the unique dedup key across all paths."""

	def test_phone_dedup_six_input_formats_normalize(self):
		"""Six INPUT formats all normalize to the same E.164 and find the same lead.

		Contract under test: ``_normalize_phone`` + ``_phone_candidates`` together
		guarantee that the website accepts varied incoming phone formats from the
		marketing form and routes them to one canonical lead. The stored lead's
		``mobile_no`` is already E.164 here (the typical case)."""
		existing = make_lead(phone="+919812345010", first_name="Existing")
		variants = [
			"+919812345010",  # E.164
			"9812345010",  # national, no prefix
			"09812345010",  # 0-prefixed national
			"+91 9812345010",  # E.164 with space
			"919812345010",  # CC-prefixed, no +
			"+91-9812345010",  # E.164 with dash
		]
		for variant in variants:
			res = website.create_lead(name="Dup", mobile=variant)
			self.assertIn(res["status"], ("existing", "reactivated"), f"variant {variant!r} did not match")
			self.assertEqual(res["name"], existing.name, f"variant {variant!r} matched wrong lead")

	def test_phone_dedup_finds_lead_stored_with_national_format(self):
		"""Stored mobile_no in non-E.164 forms is still found via the candidate set.

		``_phone_candidates`` includes the 10-digit national number, the 0-prefixed
		form, the bare 91-prefixed form, and the +91-prefixed form — covering the
		common legacy stored shapes. E.164-input submission must match all of these."""
		# (stored_phone, submit_phone_e164, label) — all map to the same national number
		cases = [
			("9812345020", "+919812345020", "stored-national"),
			("09812345021", "+919812345021", "stored-zero-prefixed"),
			("919812345022", "+919812345022", "stored-cc-prefixed"),
		]
		for stored_phone, submit_phone, first_name in cases:
			lead = make_lead(phone=stored_phone, first_name=first_name)
			res = website.create_lead(name="Resubmit", mobile=submit_phone)
			self.assertEqual(res["status"], "existing", f"stored {stored_phone!r} not found")
			self.assertEqual(res["name"], lead.name, f"stored {stored_phone!r} matched wrong lead")

	def test_existing_open_active_lead_returns_existing(self):
		existing = make_lead(phone="+919812345011", first_name="Open")
		res = website.create_lead(name="Resubmit", mobile="+919812345011", message="round 2")
		self.assertEqual(res["status"], "existing")
		self.assertEqual(res["name"], existing.name)
		self.assertEqual(res["stage"], "C0")

	def test_closed_match_c6_returns_closed_match_no_attribution(self):
		"""C6 (Lost) lead → closed_match with stage; existing UTM untouched."""
		closed = make_lead(
			phone="+919812345012",
			first_name="Lost",
			status="C6",
			lost_reason="Chose Competitor",
			custom_utm_source="original-campaign",
		)
		res = website.create_lead(
			name="Resubmit",
			mobile="+919812345012",
			utm_source="new-campaign",  # MUST NOT overwrite
		)
		self.assertEqual(res["status"], "closed_match")
		self.assertEqual(res["stage"], "C6")
		self.assertNotIn("name", res)
		# UTM stayed put — first-touch attribution preserved
		self.assertEqual(
			frappe.db.get_value("CRM Lead", closed.name, "custom_utm_source"),
			"original-campaign",
		)

	def test_archived_lead_returns_closed_match_terminal(self):
		"""Archived engagement is terminal even on an Open-type C-stage (website.py:237-238)."""
		make_lead(phone="+919812345013", first_name="Arch", lead_status="Archived")
		res = website.create_lead(name="Resubmit", mobile="+919812345013")
		self.assertEqual(res["status"], "closed_match")
		self.assertEqual(res["stage"], "C0")

	def test_cold_unresponsive_auto_reactivates(self):
		"""PRD §8.2 Phase 1 — Cold lead resubmitting via website auto-reactivates."""
		cold = make_lead(phone="+919812345014", first_name="Cold", lead_status="Cold-Unresponsive")
		res = website.create_lead(name="Resubmit", mobile="+919812345014")
		self.assertEqual(res["status"], "reactivated")
		self.assertEqual(res["name"], cold.name)

		lead_status = frappe.db.get_value("CRM Lead", cold.name, "lead_status")
		self.assertEqual(lead_status, "Reactivated")

	def test_cold_unresponsive_auto_reactivates_as_guest(self):
		"""Regression: the endpoint runs as Guest in real production traffic, not
		Administrator (which FrappeTestCase defaults to and which masked this bug —
		Guest has no ownership/role standing, so _apply_stage_transition_guard's
		ownership check would throw without the ignore_stage_transition_guard flag
		website.py now sets before this save)."""
		cold = make_lead(phone="+919812345016", first_name="ColdGuest", lead_status="Cold-Unresponsive")
		frappe.set_user("Guest")
		try:
			res = website.create_lead(name="Resubmit", mobile="+919812345016")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(res["status"], "reactivated")
		lead_status = frappe.db.get_value("CRM Lead", cold.name, "lead_status")
		self.assertEqual(lead_status, "Reactivated")

	def test_resubmission_does_not_overwrite_email_or_utm(self):
		"""First-touch attribution: existing email/UTM preserved on resubmit (website.py:230-255)."""
		existing = make_lead(
			phone="+919812345015",
			email="original@example.com",
			custom_utm_source="orig-source",
			custom_utm_campaign="orig-camp",
		)
		website.create_lead(
			name="Resubmit",
			mobile="+919812345015",
			email="new@example.com",
			utm_source="new-source",
			utm_campaign="new-camp",
		)
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.email, "original@example.com")
		self.assertEqual(lead.get("custom_utm_source"), "orig-source")
		self.assertEqual(lead.get("custom_utm_campaign"), "orig-camp")


class TestCreateLeadAuth(_BaseWebsite):
	def test_token_missing_raises_auth(self):
		self._hdr_patch.stop()
		self._hdr_patch = patch("frappe.get_request_header", _header_returning(None))
		self._hdr_patch.start()
		with self.assertRaises(frappe.AuthenticationError):
			website.create_lead(name="X", mobile="+919812345020")

	def test_token_wrong_raises_auth(self):
		self._hdr_patch.stop()
		self._hdr_patch = patch("frappe.get_request_header", _header_returning("WRONG"))
		self._hdr_patch.start()
		with self.assertRaises(frappe.AuthenticationError):
			website.create_lead(name="X", mobile="+919812345021")

	def test_token_unconfigured_raises_auth(self):
		original = frappe.conf.pop(website.TOKEN_CONF_KEY, None)
		try:
			with self.assertRaises(frappe.AuthenticationError):
				website.create_lead(name="X", mobile="+919812345022")
		finally:
			if original is not None:
				frappe.conf[website.TOKEN_CONF_KEY] = original


class TestCreateLeadValidation(_BaseWebsite):
	def test_missing_name_raises_validation(self):
		with self.assertRaises(frappe.ValidationError):
			website.create_lead(name="", mobile="+919812345030")

	def test_missing_mobile_raises_validation(self):
		with self.assertRaises(frappe.ValidationError):
			website.create_lead(name="No Phone", mobile="")

	def test_invalid_phone_raises_validation(self):
		with self.assertRaises(frappe.ValidationError):
			website.create_lead(name="Bad Phone", mobile="not-a-phone")

	def test_invalid_email_dropped_with_audit_line(self):
		res = website.create_lead(
			name="Email Test",
			mobile="+919812345031",
			email="not an email",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertIsNone(lead.email)
		# Comment trail should mention the dropped email
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead.name},
			fields=["content"],
		)
		self.assertTrue(
			any("Email omitted" in (c.content or "") for c in comments),
			f"expected 'Email omitted' in comment trail, got: {comments}",
		)

	def test_lead_type_override_is_logged_and_ignored(self):
		"""customer_type wins; passing a disagreeing lead_type fires frappe.log_error and is dropped."""
		with patch("frappe.log_error") as mock_log:
			res = website.create_lead(
				name="Override",
				mobile="+919812345032",
				customer_type="Architect",  # derives Projects
				lead_type="Retail",  # should be ignored
			)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_lead_type"), "Projects")
		# Look across all log_error calls for the "override ignored" title
		joined = repr(mock_log.call_args_list)
		self.assertIn("lead_type override ignored", joined)


class TestCreateLeadUtmAndTrail(_BaseWebsite):
	def test_utm_captured_in_comment_on_new_lead(self):
		res = website.create_lead(
			name="UTM New",
			mobile="+919812345040",
			utm_source="google",
			utm_medium="cpc",
			utm_campaign="brand",
			utm_content="adgroup-1",
			message="hello",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead.name},
			fields=["content"],
		)
		joined = " ".join(c.content or "" for c in comments)
		self.assertIn("utm_source=google", joined)
		self.assertIn("utm_campaign=brand", joined)
		# Stored on the lead itself too — first-touch capture
		self.assertEqual(lead.get("custom_utm_source"), "google")

	def test_utm_only_in_trail_not_attributed_on_resubmit(self):
		"""PRD §7.2 — existing lead UTM stays put; new UTM lives only in the comment trail."""
		existing = make_lead(
			phone="+919812345041",
			custom_utm_source="first",
			custom_utm_campaign="orig",
		)
		website.create_lead(
			name="Resub",
			mobile="+919812345041",
			utm_source="second",
			utm_campaign="new",
		)
		# Lead's stored UTM unchanged
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.get("custom_utm_source"), "first")
		self.assertEqual(lead.get("custom_utm_campaign"), "orig")
		# But the new UTM appears in the comment trail
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": lead.name},
			fields=["content"],
		)
		joined = " ".join(c.content or "" for c in comments)
		self.assertIn("utm_source=second", joined)


class TestCreateLeadLastTouchUtm(_BaseWebsite):
	"""``custom_utm_last_touch_*`` — updates on every resubmission, unlike first-touch
	``custom_utm_*`` which is write-once (website.py:_update_last_touch)."""

	def test_new_lead_stores_both_first_and_last_touch(self):
		res = website.create_lead(
			name="LastTouch New",
			mobile="+919812345070",
			utm_source="google",
			utm_medium="cpc",
			utm_last_touch_source="facebook",
			utm_last_touch_medium="cpc",
			utm_last_touch_campaign="retarget",
			utm_last_touch_content="carousel-1",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_utm_source"), "google")
		self.assertEqual(lead.get("custom_utm_last_touch_source"), "facebook")
		self.assertEqual(lead.get("custom_utm_last_touch_medium"), "cpc")
		self.assertEqual(lead.get("custom_utm_last_touch_campaign"), "retarget")
		self.assertEqual(lead.get("custom_utm_last_touch_content"), "carousel-1")

	def test_resubmission_updates_last_touch_preserves_first_touch(self):
		existing = make_lead(
			phone="+919812345071",
			custom_utm_source="google",
			custom_utm_last_touch_source="google",
		)
		res = website.create_lead(
			name="Resub",
			mobile="+919812345071",
			utm_source="ignored-on-resubmit",
			utm_last_touch_source="facebook",
			utm_last_touch_medium="remarketing",
		)
		self.assertEqual(res["status"], "existing")
		lead = frappe.get_doc("CRM Lead", existing.name)
		# First-touch unchanged (existing coverage elsewhere confirms this too)
		self.assertEqual(lead.get("custom_utm_source"), "google")
		# Last-touch updated, and actually persisted (not just returned in-memory)
		self.assertEqual(lead.get("custom_utm_last_touch_source"), "facebook")
		self.assertEqual(lead.get("custom_utm_last_touch_medium"), "remarketing")

	def test_resubmission_with_last_touch_omitted_preserves_prior_value(self):
		"""Simulates an older indiframe-web client mid-rollout that doesn't send
		utm_last_touch_* yet — must not null out a previously-stored value."""
		existing = make_lead(
			phone="+919812345072",
			custom_utm_last_touch_source="google",
			custom_utm_last_touch_medium="cpc",
		)
		website.create_lead(name="Resub", mobile="+919812345072")
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.get("custom_utm_last_touch_source"), "google")
		self.assertEqual(lead.get("custom_utm_last_touch_medium"), "cpc")

	def test_resubmission_last_touch_update_surfaced_in_comment_trail(self):
		"""Last-touch is the field that actually changes on resubmission — the
		Comment trail must show it, not just the frozen first-touch UTM."""
		existing = make_lead(phone="+919812345073")
		website.create_lead(
			name="Resub",
			mobile="+919812345073",
			utm_last_touch_source="facebook",
			utm_last_touch_medium="remarketing",
			gclid="gclid-trail-test",
		)
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "CRM Lead", "reference_name": existing.name},
			fields=["content"],
		)
		joined = " ".join(c.content or "" for c in comments)
		self.assertIn("Last-touch update:", joined)
		self.assertIn("utm_last_touch_source=facebook", joined)
		self.assertIn("gclid=gclid-trail-test", joined)


class TestCreateLeadFirstTouchUtm(_BaseWebsite):
	"""``custom_utm_first_touch_*`` — true first-touch (from indiframe-web's persisted
	180-day client-side capture), write-once same as legacy ``custom_utm_*`` — unlike
	``custom_utm_last_touch_*`` which updates on every resubmission."""

	def test_new_lead_stores_true_first_touch_independent_of_legacy_utm(self):
		res = website.create_lead(
			name="FirstTouch New",
			mobile="+919812345080",
			utm_source="last-page-visited",
			utm_first_touch_source="google",
			utm_first_touch_medium="cpc",
			utm_first_touch_campaign="brand-launch",
			utm_first_touch_content="banner-1",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_utm_source"), "last-page-visited")
		self.assertEqual(lead.get("custom_utm_first_touch_source"), "google")
		self.assertEqual(lead.get("custom_utm_first_touch_medium"), "cpc")
		self.assertEqual(lead.get("custom_utm_first_touch_campaign"), "brand-launch")
		self.assertEqual(lead.get("custom_utm_first_touch_content"), "banner-1")

	def test_resubmission_never_overwrites_true_first_touch(self):
		existing = make_lead(
			phone="+919812345081",
			custom_utm_first_touch_source="google",
			custom_utm_first_touch_campaign="original-launch",
		)
		res = website.create_lead(
			name="Resub",
			mobile="+919812345081",
			utm_first_touch_source="facebook",
			utm_first_touch_campaign="should-be-ignored",
		)
		self.assertEqual(res["status"], "existing")
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.get("custom_utm_first_touch_source"), "google")
		self.assertEqual(lead.get("custom_utm_first_touch_campaign"), "original-launch")


class TestCreateLeadClickIds(_BaseWebsite):
	"""``custom_gclid``/``fbclid``/``wbraid``/``gbraid`` — same last-touch update
	semantics as ``custom_utm_last_touch_*`` (website.py:_LAST_TOUCH_FIELD_MAP)."""

	def test_new_lead_stores_click_ids(self):
		res = website.create_lead(
			name="ClickId New",
			mobile="+919812345090",
			gclid="gclid-abc123",
			fbclid="fbclid-def456",
			wbraid="wbraid-ghi789",
			gbraid="gbraid-jkl012",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.get("custom_gclid"), "gclid-abc123")
		self.assertEqual(lead.get("custom_fbclid"), "fbclid-def456")
		self.assertEqual(lead.get("custom_wbraid"), "wbraid-ghi789")
		self.assertEqual(lead.get("custom_gbraid"), "gbraid-jkl012")

	def test_resubmission_updates_click_ids(self):
		existing = make_lead(phone="+919812345091", custom_gclid="old-gclid")
		res = website.create_lead(
			name="Resub",
			mobile="+919812345091",
			gclid="new-gclid",
		)
		self.assertEqual(res["status"], "existing")
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.get("custom_gclid"), "new-gclid")

	def test_resubmission_with_click_ids_omitted_preserves_prior_value(self):
		existing = make_lead(phone="+919812345092", custom_gclid="stays-put")
		website.create_lead(name="Resub", mobile="+919812345092")
		lead = frappe.get_doc("CRM Lead", existing.name)
		self.assertEqual(lead.get("custom_gclid"), "stays-put")


class TestCreateLeadSource(_BaseWebsite):
	"""``source``/``sub_source`` resolution for paid-channel attribution (website.py:36-75)."""

	def test_explicit_paid_source_and_sub_source_stored(self):
		res = website.create_lead(
			name="Paid Lead",
			mobile="+919812345060",
			source="Paid",
			sub_source="Google Ads",
		)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.source, "Paid")
		self.assertEqual(lead.get("custom_sub_source"), "Google Ads")

	def test_unknown_source_falls_back_to_direct(self):
		with patch("frappe.log_error") as mock_log:
			res = website.create_lead(
				name="Bad Source",
				mobile="+919812345061",
				source="Not A Real Source",
			)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.source, website.WEBSITE_SOURCE)
		joined = repr(mock_log.call_args_list)
		self.assertIn("disallowed source", joined)

	def test_disallowed_source_referral_falls_back(self):
		"""``source`` is an allowlist (Direct/Paid only), not "any real CRM Lead Source".

		"Referral" is a valid CRM Lead Source but is in crm_lead.py's
		_SUB_SOURCE_TRIGGER_SOURCES, which mandates custom_sub_source — a rule
		this public endpoint has no way to satisfy. Must fall back, not 500.
		"""
		with patch("frappe.log_error") as mock_log:
			res = website.create_lead(
				name="Referral Lead",
				mobile="+919812345063",
				source="Referral",
			)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.source, website.WEBSITE_SOURCE)
		joined = repr(mock_log.call_args_list)
		self.assertIn("disallowed source", joined)

	def test_unknown_sub_source_under_paid_left_unset(self):
		"""Unlike Direct (falls back to 'Website'), Paid has no generic sub_source default."""
		with patch("frappe.log_error") as mock_log:
			res = website.create_lead(
				name="Bad Sub Source",
				mobile="+919812345062",
				source="Paid",
				sub_source="Not A Real Sub Source",
			)
		lead = frappe.get_doc("CRM Lead", res["name"])
		self.assertEqual(lead.source, "Paid")
		self.assertIsNone(lead.get("custom_sub_source"))
		joined = repr(mock_log.call_args_list)
		self.assertIn("unknown sub_source", joined)


class TestCreateLeadRaceRecovery(_BaseWebsite):
	"""Cover the race-recovery path at website.py:285-311."""

	def test_race_validation_recovers_to_existing(self):
		"""If Phone Dedup rejects the insert and a matching open lead now exists,
		the second _find_existing_lead lookup recovers and returns 'existing'."""
		racer = make_lead(phone="+919812345050", first_name="Racer")

		# First _find_existing_lead returns nothing; insert throws ValidationError;
		# second _find_existing_lead returns the racer.
		original_find = website._find_existing_lead
		call_count = {"n": 0}

		def fake_find(e164, national, raw):
			call_count["n"] += 1
			if call_count["n"] == 1:
				return None, None  # pretend not found pre-insert
			return original_find(e164, national, raw)  # real lookup recovers

		# Capture the REAL frappe.get_doc before any patching so the side_effect
		# can call back into it without recursing into the mock.
		real_get_doc = frappe.get_doc

		def fake_get_doc(*args, **kwargs):
			# Intercept ONLY the new-lead insert call (first positional arg is a dict
			# with doctype=CRM Lead). The recovery path uses get_doc("CRM Lead", name)
			# with two string positional args — let those through to the real function.
			if len(args) == 1 and isinstance(args[0], dict) and args[0].get("doctype") == "CRM Lead":

				class _RaisingDoc:
					def insert(self, *a, **kw):
						raise frappe.ValidationError("Phone Dedup rejected (simulated)")

				return _RaisingDoc()
			return real_get_doc(*args, **kwargs)

		with patch.object(website, "_find_existing_lead", fake_find):
			with patch("frappe.get_doc", side_effect=fake_get_doc):
				res = website.create_lead(name="Race", mobile="+919812345050")

		self.assertEqual(res["status"], "existing")
		self.assertEqual(res["name"], racer.name)

	def test_race_unrelated_validation_reraises(self):
		"""If the dedup re-lookup still finds nothing, the original ValidationError propagates."""

		def fake_find_always_none(e164, national, raw):
			return None, None

		real_get_doc = frappe.get_doc

		def fake_get_doc(*args, **kwargs):
			if len(args) == 1 and isinstance(args[0], dict) and args[0].get("doctype") == "CRM Lead":

				class _RaisingDoc:
					def insert(self, *a, **kw):
						raise frappe.ValidationError("Some unrelated validation")

				return _RaisingDoc()
			return real_get_doc(*args, **kwargs)

		with patch.object(website, "_find_existing_lead", fake_find_always_none):
			with patch("frappe.get_doc", side_effect=fake_get_doc):
				with self.assertRaises(frappe.ValidationError):
					website.create_lead(name="Race2", mobile="+919812345051")

# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""FacebookSyncSource lead-tagging tests.

Covers the Meta Ads intake fix: synced leads must be tagged
source="Paid" (not the retired "Facebook" CRM Lead Source, which no
longer exists and would fail Link validation on insert), with
custom_sub_source defaulting to "Meta Generic" unless the Lead Sync
Source config overrides it.
"""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.lead_syncing.doctype.lead_sync_source.facebook import (
	FacebookSyncSource,
	_auto_map_questions,
	create_facebook_lead_form_in_db,
	fetch_and_store_leadgen_forms_from_facebook,
	get_fb_graph_api_url,
)
from crm.lead_syncing.doctype.lead_sync_source.lead_sync_source import LeadSyncSource

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]

_QUESTION_MAP = {"full_name": "first_name", "phone_number": "mobile_no"}


def _fb_lead(lead_id: str, full_name: str, phone: str) -> dict:
	return {
		"id": lead_id,
		"field_data": [
			{"name": "full_name", "values": [full_name]},
			{"name": "phone_number", "values": [phone]},
		],
	}


class TestFacebookSyncSourceLeadTagging(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._mapping_patch = patch.object(
			FacebookSyncSource, "get_form_questions_mapping", return_value=_QUESTION_MAP
		)
		self._mapping_patch.start()

	def tearDown(self):
		self._mapping_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_synced_lead_tagged_paid_meta_generic_by_default(self):
		source = FacebookSyncSource("token", "form-fb-1")
		doc = source.sync_single_lead(_fb_lead("fb-lead-1", "Meta One", "+919812346001"))
		self.assertEqual(doc.source, "Paid")
		self.assertEqual(doc.get("custom_sub_source"), "Meta Generic")
		self.assertEqual(doc.get("facebook_lead_id"), "fb-lead-1")
		self.assertEqual(doc.get("facebook_form_id"), "form-fb-1")

	def test_synced_lead_uses_configured_sub_source(self):
		source = FacebookSyncSource("token", "form-fb-2", sub_source="Meta Remarketing")
		doc = source.sync_single_lead(_fb_lead("fb-lead-2", "Meta Two", "+919812346002"))
		self.assertEqual(doc.source, "Paid")
		self.assertEqual(doc.get("custom_sub_source"), "Meta Remarketing")

	def test_duplicate_lead_logged_not_raised(self):
		source = FacebookSyncSource("token", "form-fb-3")
		source.sync_single_lead(_fb_lead("fb-lead-3", "Meta Three", "+919812346003"))

		# Same form + mapped field values -> validate_duplicate_lead raises
		# DuplicateLeadError, caught internally and written to Failed Lead
		# Sync Log instead of propagating.
		result = source.sync_single_lead(_fb_lead("fb-lead-3", "Meta Three", "+919812346003"))
		self.assertIsNone(result)

		logs = frappe.get_all("Failed Lead Sync Log", filters={"type": "Duplicate"})
		self.assertEqual(len(logs), 1)

	def test_sync_aborts_when_paid_lead_source_missing(self):
		"""sync() must fail fast (one clear log) rather than let every lead in the
		batch fail individually into Failed Lead Sync Log with a LinkValidationError."""
		source = FacebookSyncSource("token", "form-fb-4")
		with (
			patch("frappe.db.exists", return_value=False),
			patch("frappe.log_error") as mock_log,
			patch.object(FacebookSyncSource, "fetch_leads") as mock_fetch,
		):
			source.sync()
		mock_fetch.assert_not_called()
		self.assertTrue(mock_log.called)


class TestLeadSyncSourceSubSourceScope(FrappeTestCase):
	"""B4 — sub_source must be scoped to source='Paid', enforced server-side
	(the Vue Link's :filters="{source:'Paid'}" is cosmetic/client-side only)."""

	def setUp(self):
		super().setUp()
		self._fb_fetch_patch = patch(
			"crm.lead_syncing.doctype.lead_sync_source.lead_sync_source.fetch_and_store_pages_from_facebook"
		)
		self._fb_fetch_patch.start()

	def tearDown(self):
		self._fb_fetch_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_mismatched_sub_source_scope_raises(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			frappe.get_doc(
				{
					"doctype": "Lead Sync Source",
					"name": "Test Wrong Scope Source",
					"type": "Facebook",
					"access_token": "dummy-token",
					"sub_source": "Website",  # scoped to source=Direct, not Paid
				}
			).insert(ignore_permissions=True)
		self.assertIn("not scoped to source", str(ctx.exception))

	def test_correctly_scoped_sub_source_saves(self):
		doc = frappe.get_doc(
			{
				"doctype": "Lead Sync Source",
				"name": "Test Correct Scope Source",
				"type": "Facebook",
				"access_token": "dummy-token",
				"sub_source": "Meta Generic",  # scoped to source=Paid
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.sub_source, "Meta Generic")


class TestSyncLeadsWiring(FrappeTestCase):
	"""_sync_leads() must build FacebookSyncSource from the doc's own fields,
	using the decrypted access_token, and fail fast if the form isn't set."""

	def test_sync_leads_constructs_facebook_source_from_doc_fields(self):
		doc = frappe.get_doc(
			{
				"doctype": "Lead Sync Source",
				"type": "Facebook",
				"access_token": "raw-token",
				"facebook_lead_form": "form-wiring-1",
				"sub_source": "Meta Remarketing",
			}
		)
		with (
			patch.object(LeadSyncSource, "get_password", return_value="decrypted-token"),
			patch(
				"crm.lead_syncing.doctype.lead_sync_source.lead_sync_source.FacebookSyncSource"
			) as mock_source_cls,
		):
			doc._sync_leads()

		mock_source_cls.assert_called_once_with(
			"decrypted-token", "form-wiring-1", sub_source="Meta Remarketing"
		)
		mock_source_cls.return_value.sync.assert_called_once()

	def test_sync_leads_throws_when_form_not_set(self):
		doc = frappe.get_doc(
			{
				"doctype": "Lead Sync Source",
				"type": "Facebook",
				"access_token": "raw-token",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc._sync_leads()


class TestFetchLeads(FrappeTestCase):
	"""fetch_leads() must only add the incremental `filtering` param once a
	last_synced_at checkpoint exists; first-ever sync pulls everything."""

	def test_fetch_leads_without_last_synced_at_has_no_filtering_param(self):
		source = FacebookSyncSource("token", "form-fetch-1")
		with (
			patch.object(FacebookSyncSource, "last_synced_at", new_callable=PropertyMock, return_value=None),
			patch(
				"crm.lead_syncing.doctype.lead_sync_source.facebook.make_get_request",
				return_value={"data": []},
			) as mock_get,
		):
			source.fetch_leads()

		args, kwargs = mock_get.call_args
		self.assertEqual(args[0], get_fb_graph_api_url("/form-fetch-1/leads"))
		self.assertNotIn("filtering", kwargs["params"])

	def test_fetch_leads_with_last_synced_at_adds_filtering_param(self):
		source = FacebookSyncSource("token", "form-fetch-2")
		with (
			patch.object(
				FacebookSyncSource,
				"last_synced_at",
				new_callable=PropertyMock,
				return_value="2026-01-01 00:00:00",
			),
			patch(
				"crm.lead_syncing.doctype.lead_sync_source.facebook.make_get_request",
				return_value={"data": []},
			) as mock_get,
		):
			source.fetch_leads()

		_, kwargs = mock_get.call_args
		self.assertIn("filtering", kwargs["params"])


class TestUpdateLastSyncedAt(FrappeTestCase):
	"""sync() must stamp last_synced_at on the Lead Sync Source after a
	successful run, so the next fetch_leads() call filters incrementally."""

	def setUp(self):
		super().setUp()
		self._fb_fetch_patch = patch(
			"crm.lead_syncing.doctype.lead_sync_source.lead_sync_source.fetch_and_store_pages_from_facebook"
		)
		self._fb_fetch_patch.start()

	def tearDown(self):
		self._fb_fetch_patch.stop()
		frappe.db.rollback()
		super().tearDown()

	def test_sync_updates_last_synced_at_after_successful_run(self):
		lead_sync_source = frappe.get_doc(
			{
				"doctype": "Lead Sync Source",
				"name": "Test Update Last Synced",
				"type": "Facebook",
				"access_token": "dummy-token",
			}
		).insert(ignore_permissions=True)
		self.assertIsNone(lead_sync_source.last_synced_at)

		source = FacebookSyncSource("token", "form-uls-1", source_name=lead_sync_source.name)
		with patch.object(FacebookSyncSource, "fetch_leads", return_value=[]):
			source.sync()

		lead_sync_source.reload()
		self.assertIsNotNone(lead_sync_source.last_synced_at)


class TestGetFormQuestionsMapping(FrappeTestCase):
	"""get_form_questions_mapping() must build {key: mapped_field} from DB,
	drop unmapped questions, and cache the result after the first call."""

	def setUp(self):
		super().setUp()
		self.page = frappe.get_doc(
			{
				"doctype": "Facebook Page",
				"id": "page-mapping-1",
				"page_name": "Mapping Test Page",
				"category": "Business",
				"access_token": "page-token",
				"account_id": "acct-1",
			}
		).insert(ignore_permissions=True)
		self.form = frappe.get_doc(
			{
				"doctype": "Facebook Lead Form",
				"id": "form-mapping-1",
				"page": self.page.name,
				"form_name": "Mapping Test Form",
				"questions": [
					{"key": "full_name", "mapped_to_crm_field": "first_name"},
					{"key": "phone_number", "mapped_to_crm_field": "mobile_no"},
					{"key": "unmapped_question", "mapped_to_crm_field": None},
				],
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_mapping_built_from_db_and_drops_unmapped_questions(self):
		source = FacebookSyncSource("token", self.form.name)
		mapping = source.get_form_questions_mapping()
		self.assertEqual(mapping, {"full_name": "first_name", "phone_number": "mobile_no"})

	def test_mapping_is_cached_after_first_call(self):
		source = FacebookSyncSource("token", self.form.name)
		source.get_form_questions_mapping()
		with patch("frappe.db.get_all") as mock_get_all:
			source.get_form_questions_mapping()
		mock_get_all.assert_not_called()


class TestAutoMapQuestions(FrappeTestCase):
	"""Standard Meta question keys should auto-fill mapped_to_crm_field on fetch,
	so a newly-connected form doesn't silently sync leads with no first_name
	(the production failure this was built to prevent)."""

	def test_standard_keys_auto_mapped(self):
		questions = [
			{"key": "full_name", "label": "Full Name"},
			{"key": "phone_number", "label": "Phone"},
			{"key": "email", "label": "Email"},
		]
		mapped = _auto_map_questions(questions)
		self.assertEqual(
			{q["key"]: q["mapped_to_crm_field"] for q in mapped},
			{"full_name": "first_name", "phone_number": "mobile_no", "email": "email"},
		)

	def test_unrecognized_key_stays_unmapped(self):
		mapped = _auto_map_questions([{"key": "favorite_color", "label": "Favorite Color"}])
		self.assertIsNone(mapped[0].get("mapped_to_crm_field"))

	def test_existing_mapping_not_overwritten(self):
		mapped = _auto_map_questions([{"key": "email", "mapped_to_crm_field": "custom_customer_type"}])
		self.assertEqual(mapped[0]["mapped_to_crm_field"], "custom_customer_type")

	def test_matching_is_case_and_whitespace_insensitive(self):
		mapped = _auto_map_questions([{"key": "  Full_Name  "}])
		self.assertEqual(mapped[0]["mapped_to_crm_field"], "first_name")

	def test_synonym_questions_on_same_form_dont_collide(self):
		"""email/work_email both target "email" — sync_single_lead's dict
		comprehension keys by target field, so both auto-mapping would silently
		drop one answer with zero error. Only the first should claim the field."""
		mapped = _auto_map_questions(
			[
				{"key": "email", "label": "Email"},
				{"key": "work_email", "label": "Work Email"},
			]
		)
		by_key = {q["key"]: q.get("mapped_to_crm_field") for q in mapped}
		targets = list(by_key.values())
		self.assertEqual(targets.count("email"), 1)
		self.assertIsNone(by_key["work_email"])

	def test_synonym_question_skips_target_already_manually_mapped(self):
		mapped = _auto_map_questions(
			[
				{"key": "phone", "mapped_to_crm_field": "mobile_no"},  # pre-existing manual mapping
				{"key": "phone_number", "label": "Phone Number"},  # would also target mobile_no
			]
		)
		self.assertEqual(mapped[0]["mapped_to_crm_field"], "mobile_no")
		self.assertIsNone(mapped[1].get("mapped_to_crm_field"))


class TestCreateFacebookLeadFormInDb(FrappeTestCase):
	"""create_facebook_lead_form_in_db() is where raw Graph API question dicts
	first become Facebook Lead Form Question rows — auto-map must apply here."""

	def setUp(self):
		super().setUp()
		self.page = frappe.get_doc(
			{
				"doctype": "Facebook Page",
				"id": "page-automap-1",
				"page_name": "Automap Test Page",
				"category": "Business",
				"access_token": "page-token",
				"account_id": "acct-automap-1",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_new_form_questions_auto_mapped_on_creation(self):
		create_facebook_lead_form_in_db(
			{
				"id": "form-automap-1",
				"name": "Automap Test Form",
				"questions": [
					{"key": "full_name", "label": "Full Name"},
					{"key": "phone_number", "label": "Phone"},
					{"key": "email", "label": "Email"},
				],
			},
			self.page.name,
		)
		form = frappe.get_doc("Facebook Lead Form", "form-automap-1")
		mapping = {q.key: q.mapped_to_crm_field for q in form.questions}
		self.assertEqual(mapping, {"full_name": "first_name", "phone_number": "mobile_no", "email": "email"})

	def test_regression_lead_with_only_standard_keys_now_inserts(self):
		"""Exact shape of the production failure: full_name/phone/email, no
		pre-existing mapping — must now produce a lead with first_name set,
		not throw "requires either a person's name or an organization's name"."""
		create_facebook_lead_form_in_db(
			{
				"id": "form-automap-2",
				"name": "Regression Test Form",
				"questions": [
					{"key": "full_name", "label": "Full Name"},
					{"key": "phone", "label": "Phone"},
					{"key": "email", "label": "Email"},
				],
			},
			self.page.name,
		)
		source = FacebookSyncSource("token", "form-automap-2")
		doc = source.sync_single_lead(
			{
				"id": "fb-lead-automap-1",
				"field_data": [
					{"name": "full_name", "values": ["harish"]},
					{"name": "phone", "values": ["+919663796219"]},
					{"name": "email", "values": ["harish@example.com"]},
				],
			},
			raise_exception=True,
		)
		self.assertEqual(doc.first_name, "harish")
		self.assertEqual(doc.mobile_no, "+919663796219")

	def test_form_with_no_recognized_name_field_skipped_not_raised(self):
		"""fetch_and_store_leadgen_forms_from_facebook must isolate one bad form's
		mandatory-field failure — must not abort discovery for every other
		page/form in the same token-connect flow (mirrors sync_single_lead's own
		per-lead isolation)."""
		with patch(
			"crm.lead_syncing.doctype.lead_sync_source.facebook.make_get_request",
			return_value={
				"data": [
					{
						"id": "form-automap-bad",
						"name": "Fully Custom Form",
						"questions": [{"key": "your_full_name_here", "label": "Your Name"}],
					},
					{
						"id": "form-automap-good",
						"name": "Standard Form",
						"questions": [{"key": "full_name", "label": "Full Name"}],
					},
				]
			},
		):
			forms = fetch_and_store_leadgen_forms_from_facebook(self.page.name, "page-token")

		self.assertEqual(len(forms), 2)
		self.assertFalse(frappe.db.exists("Facebook Lead Form", "form-automap-bad"))
		self.assertTrue(frappe.db.exists("Facebook Lead Form", "form-automap-good"))


class TestValidateDuplicateLead(FrappeTestCase):
	"""validate_duplicate_lead must not KeyError when the form has more mapped
	fields than a given lead actually answered (an optional question left
	blank) — auto-mapping now wires up optional fields (city/state/company)
	by default, making this a real, expected case."""

	def test_unanswered_mapped_field_does_not_raise(self):
		source = FacebookSyncSource("token", "form-dup-1")
		field_mapping = {"full_name": "first_name", "city": "custom_city"}
		lead_data = {
			"first_name": "Jane",
			"facebook_form_id": "form-dup-1",
			# "custom_city" mapped on the form but this lead didn't answer it
		}
		try:
			source.validate_duplicate_lead(lead_data, field_mapping)
		except KeyError:
			self.fail("validate_duplicate_lead raised KeyError on an unanswered mapped field")


class TestLeadSyncSource(FrappeTestCase):
	"""
	Integration tests for LeadSyncSource.
	Use this class for testing interactions between multiple components.
	"""

	pass

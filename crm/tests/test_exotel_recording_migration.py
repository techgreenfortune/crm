import csv
import io
import tempfile
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
import requests
from botocore.exceptions import ClientError
from frappe.tests.utils import FrappeTestCase

from crm.fcrm.doctype.crm_call_log.crm_call_log import CRMCallLog
from crm.scripts import migrate_exotel_recordings as migration

MODULE = "crm.scripts.migrate_exotel_recordings"


def call(**updates):
	return {
		"Sid": "old-call",
		"Direction": "outbound-api",
		"Status": "completed",
		"From": "08012345678",
		"To": "9876543210",
		"DateCreated": "2026-06-01 12:00:00",
		"StartTime": "2026-06-01 12:00:00",
		"Details": {"Leg1Status": "completed", "Leg2Status": "completed", "ConversationDuration": 17},
		"PreSignedRecordingUrl": "https://recordings.example/old-call.mp3?Signature=secret",
		**updates,
	}


class RecordingConfig:
	def setUp(self):
		super().setUp()
		config = patch.dict(
			frappe.conf,
			{
				"crm_recording_bucket": "bucket",
				"crm_recording_prefix": "recordings",
			},
		)
		config.start()
		self.addCleanup(config.stop)


class TestExotelMigrationPlan(RecordingConfig, TestCase):
	def _process(self, dry_run=True, **updates):
		return migration._process_call(
			call(**updates),
			MagicMock(),
			"bucket",
			"recordings",
			"old-account",
			dry_run,
		)

	def test_dry_run_reports_actions_without_writes(self):
		with ExitStack() as stack:
			stack.enter_context(
				patch(f"{MODULE}._resolve_call", return_value=(None, [("CRM Lead", "LEAD-1")]))
			)
			stack.enter_context(patch(f"{MODULE}._s3_has", return_value=False))
			stack.enter_context(patch(f"{MODULE}._attachment_exists", return_value=False))
			upload = stack.enter_context(patch(f"{MODULE}._upload"))
			upsert = stack.enter_context(patch(f"{MODULE}._upsert_call_log"))
			attach = stack.enter_context(patch(f"{MODULE}._attach_to_reference"))
			db = stack.enter_context(patch(f"{MODULE}.frappe.db"))
			row = self._process()
		self.assertEqual(row["result"], "planned")
		self.assertEqual(row["upload_action"], "upload")
		self.assertEqual(row["log_action"], "create")
		self.assertEqual(row["attachment_action"], "create:1")
		self.assertEqual(row["s3_url"], "s3://bucket/recordings/old-account/old-call.mp3")
		self.assertEqual(row["call_time"], "2026-06-01 12:00:00")
		for operation in (upload, upsert, attach, db.commit, db.rollback, db.savepoint):
			operation.assert_not_called()

	def test_null_recording_is_reported_without_phone_lookup(self):
		with patch(f"{MODULE}._resolve_call") as resolve:
			row = self._process(PreSignedRecordingUrl="null")
		self.assertEqual(row["result"], "skipped")
		resolve.assert_not_called()

	def test_legacy_recording_url_is_included_in_dry_run_without_download(self):
		with (
			patch(f"{MODULE}._resolve_call", return_value=(None, [("CRM Lead", "LEAD-1")])),
			patch(f"{MODULE}._s3_has", return_value=False),
			patch(f"{MODULE}._attachment_exists", return_value=False),
			patch(f"{MODULE}._upload") as upload,
		):
			row = self._process(
				PreSignedRecordingUrl="null",
				RecordingUrl="https://recordings.exotel.com/legacy.mp3",
			)
		self.assertEqual(row["result"], "planned", row["detail"])
		self.assertEqual(row["source_field"], "RecordingUrl")
		self.assertEqual(row["upload_action"], "upload")
		upload.assert_not_called()

	def test_presigned_recording_takes_priority_over_legacy_url(self):
		with (
			patch(f"{MODULE}._resolve_call", return_value=(None, [("CRM Lead", "LEAD-1")])),
			patch(f"{MODULE}._s3_has", return_value=False),
			patch(f"{MODULE}._attachment_exists", return_value=False),
		):
			row = self._process(RecordingUrl="https://recordings.exotel.com/legacy.mp3")
		self.assertEqual(row["result"], "planned", row["detail"])
		self.assertEqual(row["source_field"], "PreSignedRecordingUrl")

	def test_unmatched_call_is_error_without_upload(self):
		with (
			patch(f"{MODULE}._resolve_call", return_value=(None, [])),
			patch(f"{MODULE}._s3_has", return_value=False),
			patch(f"{MODULE}._upload") as upload,
		):
			row = self._process(dry_run=False)
		self.assertEqual(row["result"], "error")
		self.assertIn("No matching", row["detail"])
		upload.assert_not_called()

	def test_failure_rolls_back_only_call_savepoint(self):
		with (
			patch(f"{MODULE}._resolve_call", return_value=(None, [("CRM Lead", "LEAD-1")])),
			patch(f"{MODULE}._s3_has", return_value=True),
			patch(f"{MODULE}._attachment_exists", return_value=False),
			patch(f"{MODULE}._upsert_call_log", side_effect=ValueError("insert failed")),
			patch(f"{MODULE}.frappe.db") as db,
		):
			row = self._process(dry_run=False)
		self.assertEqual(row["result"], "error")
		db.rollback.assert_called_once_with(save_point="exotel_recording_call")
		db.commit.assert_not_called()

	def test_unknown_status_and_direction_raise(self):
		with self.assertRaisesRegex(ValueError, "Unsupported historical"):
			migration._status(call(Status="unknown"))
		with self.assertRaisesRegex(ValueError, "Unsupported Exotel direction"):
			migration._customer_number(call(Direction="unknown"))

	def test_inbound_missed_call_is_not_completed(self):
		data = call(
			Direction="inbound",
			Details={
				"ConversationDuration": 0,
				"Leg1Status": "completed",
				"Leg2Status": "no-answer",
			},
		)
		self.assertEqual(migration._status(data), "Call Not Answered")
		self.assertEqual(migration._duration(data), 0)

	def test_outbound_cancellation_uses_agent_leg_details(self):
		data = call(
			Status="failed",
			Details={
				"ConversationDuration": 0,
				"Leg1Status": "completed",
				"Leg2Status": "canceled",
			},
		)
		self.assertEqual(migration._status(data), "Canceled")

	def test_zero_conversation_duration_never_falls_back_to_total_duration(self):
		data = call(
			Duration=95,
			ConversationDuration=88,
			Details={
				"ConversationDuration": 0,
				"Leg1Status": "completed",
				"Leg2Status": "no-answer",
			},
		)
		self.assertEqual(migration._duration(data), 0)

	def test_existing_links_take_precedence_over_phone_match(self):
		log = frappe._dict(
			name="renamed-log",
			telephony_medium="Exotel",
			type="Outgoing",
			to="9876543210",
			recording_url="https://exotel.example/recording",
			reference_doctype="CRM Lead",
			reference_docname="ORIGINAL",
			links=[frappe._dict(link_doctype="CRM Lead", link_name="ORIGINAL")],
		)
		with (
			patch(f"{MODULE}.frappe.db.get_value", return_value=log.name),
			patch(f"{MODULE}.frappe.get_doc", return_value=log),
			patch(f"{MODULE}.get_contact_by_phone_number") as match,
		):
			existing, references = migration._resolve_call(call(), "old-call", "9876543210", "s3://b/k")
		self.assertIs(existing, log)
		self.assertEqual(references, [("CRM Lead", "ORIGINAL")])
		match.assert_not_called()

	def test_conflicting_existing_log_is_rejected(self):
		log = frappe._dict(
			name="existing",
			telephony_medium="Twilio",
			type="Outgoing",
			to="9876543210",
		)
		with (
			patch(f"{MODULE}.frappe.db.get_value", return_value=log.name),
			patch(f"{MODULE}.frappe.get_doc", return_value=log),
			self.assertRaisesRegex(ValueError, "non-Exotel"),
		):
			migration._resolve_call(call(), "old-call", "9876543210", "s3://b/k")


class TestExotelMigrationAPI(RecordingConfig, TestCase):
	def test_missing_detail_fields_are_fetched_for_dry_run_status_and_duration(self):
		session = MagicMock()
		session.get.return_value.json.return_value = {
			"Call": call(
				Direction="inbound",
				AccountSid="old-account",
				Details={"ConversationDuration": 0, "Leg1Status": "completed", "Leg2Status": "no-answer"},
			)
		}
		with (
			patch(f"{MODULE}._resolve_call", return_value=(None, [("CRM Lead", "LEAD-1")])),
			patch(f"{MODULE}._s3_has", return_value=False),
			patch(f"{MODULE}._attachment_exists", return_value=False),
			patch(f"{MODULE}._upload") as upload,
		):
			row = migration._process_call(
				call(Details=None),
				MagicMock(),
				"bucket",
				"recordings",
				"old-account",
				True,
				session=session,
			)
		self.assertEqual(row["result"], "planned", row["detail"])
		self.assertEqual(row["status"], "Call Not Answered")
		self.assertEqual(row["duration"], 0)
		upload.assert_not_called()

	def test_detail_lookup_uses_supplied_old_account_session(self):
		session = MagicMock()
		session.auth = ("old-key", "old-token")
		bulk = call(Details=None)
		session.get.return_value.json.return_value = {"Call": call(AccountSid="old-account")}
		detailed = migration._with_call_details(bulk, session, "api.exotel.com", "old-account")
		session.get.assert_called_once_with(
			"https://api.exotel.com/v1/Accounts/old-account/Calls/old-call.json",
			params={"details": "true"},
			timeout=migration.TIMEOUT,
		)
		self.assertEqual(detailed["Details"]["ConversationDuration"], 17)
		self.assertEqual(detailed["PreSignedRecordingUrl"], bulk["PreSignedRecordingUrl"])

	def test_mismatched_detail_response_is_rejected(self):
		session = MagicMock()
		session.get.return_value.json.return_value = {"Call": call(Sid="other-call")}
		with self.assertRaisesRegex(ValueError, "mismatched CallSid"):
			migration._with_call_details(call(Details=None), session, "api.exotel.com", "old-account")

	def test_nested_envelope_pagination_and_final_boundary(self):
		session = MagicMock()
		first, second = MagicMock(), MagicMock()
		first.json.return_value = {
			"RestResponse": {
				"Calls": [call(DateCreated="2026-09-30 23:59:59.500000")],
				"Metadata": {"NextPageUri": "/v1/Accounts/old/Calls.json?Page=1"},
			}
		}
		second.json.return_value = {
			"RestResponse": {
				"Calls": [call(Sid="outside", DateCreated="2026-10-01 00:00:00")],
				"Metadata": {"NextPageUri": None},
			}
		}
		session.get.side_effect = [first, second]
		result = list(
			migration._iter_calls(
				session,
				"api.exotel.com",
				"old",
				date(2026, 9, 30),
				datetime(2026, 10, 1),
			)
		)
		self.assertEqual([c["Sid"] for c in result], ["old-call"])
		self.assertEqual(session.get.call_count, 2)
		self.assertIn("RecordingUrlValidity=60", session.get.call_args.args[0])
		self.assertEqual(
			session.get.call_args_list[0].kwargs["params"]["DateCreated"],
			"gte:2026-09-30 00:00:00;lte:2026-10-01 00:00:00",
		)

	def test_daily_windows_include_midnight_call_once(self):
		session = MagicMock()
		first, second = MagicMock(), MagicMock()
		boundary = call(DateCreated="2026-06-02 00:00:00")
		first.json.return_value = {"Calls": [boundary]}
		second.json.return_value = {"Calls": [boundary]}
		session.get.side_effect = [first, second]
		result = list(
			migration._iter_calls(
				session,
				"api.exotel.com",
				"old",
				date(2026, 6, 1),
				datetime(2026, 6, 3),
			)
		)
		self.assertEqual(len(result), 1)
		self.assertEqual(session.get.call_count, 2)
		self.assertEqual(
			session.get.call_args.kwargs["params"]["DateCreated"],
			"gte:2026-06-02 00:00:00;lte:2026-06-03 00:00:00",
		)

	def test_malformed_api_response_is_not_success(self):
		with self.assertRaisesRegex(ValueError, "Calls list missing"):
			migration._extract_calls({"RestResponse": {"Error": "bad request"}})

	def test_foreign_pagination_host_is_rejected(self):
		with self.assertRaisesRegex(ValueError, "configured HTTPS host"):
			migration._next_url({"Metadata": {"NextPageUri": "https://other.example/page"}}, "api.exotel.com")

	def test_retry_policy_covers_transient_responses(self):
		with migration._session() as session:
			retry = session.get_adapter("https://").max_retries
		self.assertEqual(retry.total, 4)
		self.assertIn(429, retry.status_forcelist)
		self.assertIn(503, retry.status_forcelist)
		self.assertTrue(retry.respect_retry_after_header)

	def test_report_survives_api_error_and_relative_path_works(self):
		def broken_pages(*args):
			yield call(PreSignedRecordingUrl="null")
			raise requests.HTTPError("Exotel unavailable")

		with tempfile.TemporaryDirectory() as tmp:
			# A relative filename must not require a nonempty parent directory.
			report = Path(tmp) / "report.csv"
			with (
				patch("boto3.client"),
				patch(f"{MODULE}._iter_calls", side_effect=broken_pages),
				patch(f"{MODULE}.os.path.dirname", return_value=""),
				redirect_stdout(io.StringIO()) as output,
				self.assertRaises(requests.HTTPError),
			):
				migration.run(
					"old",
					"key",
					"token",
					"2026-06-01",
					"2026-09-30",
					"bucket",
					prefix="recordings",
					report_path=str(report),
				)
			with report.open() as handle:
				rows = list(csv.DictReader(handle))
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["result"], "skipped")
			self.assertIn('"aborted": true', output.getvalue())

	def test_limit_counts_calls_without_recordings(self):
		with tempfile.TemporaryDirectory() as tmp:
			with (
				patch("boto3.client"),
				patch(
					f"{MODULE}._iter_calls",
					return_value=iter(
						[
							call(PreSignedRecordingUrl="null"),
							call(Sid="second", PreSignedRecordingUrl="null"),
						]
					),
				),
				redirect_stdout(io.StringIO()),
			):
				result = migration.run(
					"old",
					"key",
					"token",
					"2026-06-01",
					"2026-09-30",
					"bucket",
					prefix="recordings",
					limit=1,
					report_path=str(Path(tmp) / "report.csv"),
				)
		self.assertEqual(result["total"], 1)
		self.assertEqual(result["counts"], {"skipped": 1})


class TestExotelMigrationUpload(TestCase):
	def test_legacy_recording_retries_401_using_old_account_auth(self):
		denied, audio = MagicMock(status_code=401), MagicMock(status_code=200)
		denied.__enter__.return_value = denied
		audio.__enter__.return_value = audio
		download = MagicMock()
		download.get.side_effect = [denied, audio]
		url = "https://recordings.exotel.com/legacy.mp3"
		with migration._recording_response(download, url, ("old-key", "old-token")) as response:
			self.assertIs(response, audio)
		self.assertEqual(download.get.call_count, 2)
		self.assertNotIn("auth", download.get.call_args_list[0].kwargs)
		self.assertEqual(download.get.call_args_list[1].kwargs["auth"], ("old-key", "old-token"))
		denied.__exit__.assert_called_once()
		audio.raise_for_status.assert_called_once()

	def test_auth_is_never_sent_to_signed_or_untrusted_recording_urls(self):
		for url in (
			"https://recordings.exotel.com/legacy.mp3?Signature=secret",
			"https://recordings.exotel.com.evil.example/legacy.mp3",
			"https://storage.example/legacy.mp3",
			"http://recordings.exotel.com/legacy.mp3",
		):
			denied = MagicMock(status_code=401)
			denied.__enter__.return_value = denied
			denied.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
			download = MagicMock()
			download.get.return_value = denied
			with self.subTest(url=url), self.assertRaises(requests.HTTPError):
				with migration._recording_response(download, url, ("old-key", "old-token")):
					self.fail("Unauthorized download must raise")
			self.assertEqual(download.get.call_count, 1)
			self.assertNotIn("auth", download.get.call_args.kwargs)

	def test_public_legacy_recording_does_not_send_credentials(self):
		audio = MagicMock(status_code=200)
		audio.__enter__.return_value = audio
		download = MagicMock()
		download.get.return_value = audio
		with migration._recording_response(
			download,
			"https://recordings.exotel.com/legacy.mp3",
			("old-key", "old-token"),
		) as response:
			self.assertIs(response, audio)
		self.assertEqual(download.get.call_count, 1)
		self.assertNotIn("auth", download.get.call_args.kwargs)

	def test_download_length_mismatch_never_uploads(self):
		response = MagicMock()
		response.headers = {"Content-Length": "10"}
		response.iter_content.return_value = [b"short"]
		session = MagicMock()
		session.get.return_value.__enter__.return_value = response
		s3 = MagicMock()
		with (
			patch(f"{MODULE}._session") as create_session,
			self.assertRaisesRegex(ValueError, "Incomplete recording"),
		):
			create_session.return_value.__enter__.return_value = session
			migration._upload("https://example.com/file", s3, "bucket", "key", "old", "sid")
		s3.upload_fileobj.assert_not_called()

	def test_verified_download_has_checksum_metadata_and_no_basic_auth(self):
		response = MagicMock()
		response.headers = {"Content-Length": "5", "Content-Type": "audio/mpeg"}
		response.iter_content.return_value = [b"audio"]
		session = MagicMock()
		session.get.return_value.__enter__.return_value = response
		s3 = MagicMock()
		with (
			patch(f"{MODULE}._session") as create_session,
			patch(f"{MODULE}._s3_has", return_value=True),
		):
			create_session.return_value.__enter__.return_value = session
			migration._upload("https://example.com/file", s3, "bucket", "key", "old", "sid")
		self.assertNotIn("auth", session.get.call_args.kwargs)
		meta = s3.upload_fileobj.call_args.kwargs["ExtraArgs"]["Metadata"]
		self.assertEqual(meta["bytes"], "5")
		self.assertEqual(meta["exotel-account"], "old")
		self.assertEqual(len(meta["sha256"]), 64)

	def test_unknown_existing_object_is_not_reused(self):
		s3 = MagicMock()
		s3.head_object.return_value = {"ContentLength": 10, "Metadata": {}}
		with self.assertRaisesRegex(ValueError, "manual review"):
			migration._s3_has(s3, "b", "key", "old", "sid")

	def test_only_missing_object_returns_false(self):
		s3 = MagicMock()
		s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
		self.assertFalse(migration._s3_has(s3, "b", "key", "old", "sid"))
		s3.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
		with self.assertRaises(ClientError):
			migration._s3_has(s3, "b", "key", "old", "sid")


class TestExotelMigrationFrappe(RecordingConfig, FrappeTestCase):
	def test_sales_users_cannot_assign_replace_or_remove_s3_locations(self):
		for previous, current in (
			("", "s3://bucket/recordings/call.mp3"),
			("s3://bucket/recordings/call.mp3", "s3://bucket/recordings/other.mp3"),
			("s3://bucket/recordings/call.mp3", "https://other.example/recording"),
			("s3://bucket/recordings/call.mp3", ""),
		):
			doc = CRMCallLog({"doctype": "CRM Call Log", "recording_url": current})
			with (
				self.subTest(previous=previous, current=current),
				patch.object(doc, "get_doc_before_save", return_value=frappe._dict(recording_url=previous)),
				patch.dict(frappe.session, {"user": "sales@example.com"}),
				patch("crm.permissions.recordings.frappe.get_roles", return_value=["Sales User"]),
				self.assertRaises(frappe.PermissionError),
			):
				doc.before_save()

	def test_sales_users_can_save_other_fields_without_changing_s3_location(self):
		url = "s3://bucket/recordings/call.mp3"
		doc = CRMCallLog({"doctype": "CRM Call Log", "recording_url": url})
		with (
			patch.object(doc, "get_doc_before_save", return_value=frappe._dict(recording_url=url)),
			patch.dict(frappe.session, {"user": "sales@example.com"}),
			patch("crm.permissions.recordings.frappe.get_roles", return_value=["Sales User"]),
		):
			doc.before_save()

	def test_absolute_file_url_passes_real_validation(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "historical-call.mp3",
				"file_url": migration._attachment_url("test-call"),
				"is_private": 1,
			}
		)
		file.insert(ignore_permissions=True)
		self.assertTrue(file.file_url.startswith(("http://", "https://")))

	def test_historical_log_suppresses_automation_but_normal_log_does_not(self):
		log = CRMCallLog({"doctype": "CRM Call Log"})
		with (
			patch.object(CRMCallLog, "_run_disposition_stage_move") as stage,
			patch.object(CRMCallLog, "_trigger_no_answer_retry") as retry,
		):
			log.flags.crm_historical_import = True
			log.after_save()
			stage.assert_not_called()
			retry.assert_not_called()
			log.flags.crm_historical_import = False
			log.after_save()
			stage.assert_called_once()
			retry.assert_called_once()

	def test_historical_log_insert_and_rerun_preserve_single_link_and_attachment(self):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Recording migration test",
				"mobile_no": "9876543210",
			}
		).insert(ignore_permissions=True)
		sid = "test-recording-" + frappe.generate_hash(length=12)
		references = [("CRM Lead", lead.name)]
		data = call(Sid=sid)
		name = migration._upsert_call_log(data, sid, "s3://bucket/recordings/key.mp3", None, references)
		migration._attach_to_reference("CRM Lead", lead.name, name, sid)
		log = frappe.get_doc("CRM Call Log", name)
		migration._upsert_call_log(data, sid, "s3://bucket/recordings/key.mp3", log, references)
		self.assertEqual(len(log.links), 1)
		self.assertTrue(migration._attachment_exists("CRM Lead", lead.name, name))
		self.assertEqual(
			frappe.db.get_value("CRM Call Log", name, "recording_url"), "s3://bucket/recordings/key.mp3"
		)
		self.assertEqual(log.duration, 17)
		self.assertEqual(log.creation, datetime(2026, 6, 1, 12, 0))

	def test_existing_call_creation_is_preserved_when_recording_is_updated(self):
		data = call(Sid="test-existing-" + frappe.generate_hash(length=12))
		log = frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": data["Sid"],
				"telephony_medium": "Exotel",
				"from": data["From"],
				"to": data["To"],
				"type": "Outgoing",
				"status": "Completed",
			}
		).insert(ignore_permissions=True)
		original_creation = log.creation
		migration._upsert_call_log(data, data["Sid"], "s3://bucket/recordings/existing.mp3", log, [])
		self.assertEqual(log.creation, original_creation)

	def test_apply_then_rerun_creates_one_log_and_one_attachment(self):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Recording migration apply test",
				"mobile_no": "9876543210",
			}
		).insert(ignore_permissions=True)
		sid = "test-recording-" + frappe.generate_hash(length=12)
		with (
			patch(f"{MODULE}.get_contact_by_phone_number", return_value={"lead": lead.name}),
			patch(f"{MODULE}._s3_has", return_value=True),
			patch.object(frappe.db, "commit") as commit,
		):
			first = migration._process_call(call(Sid=sid), MagicMock(), "bucket", "recordings", "old", False)
			second = migration._process_call(call(Sid=sid), MagicMock(), "bucket", "recordings", "old", False)
		self.assertEqual(first["result"], "saved", first["detail"])
		self.assertEqual(second["result"], "saved", second["detail"])
		self.assertEqual(second["log_action"], "unchanged")
		self.assertEqual(second["attachment_action"], "create:0")
		self.assertEqual(frappe.db.count("CRM Call Log", {"id": sid}), 1)
		self.assertEqual(
			frappe.db.count(
				"File",
				{
					"attached_to_doctype": "CRM Lead",
					"attached_to_name": lead.name,
					"file_url": migration._attachment_url(sid),
				},
			),
			1,
		)
		self.assertEqual(commit.call_count, 2)

	def test_call_failure_preserves_earlier_uncommitted_work(self):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Recording migration savepoint test",
				"mobile_no": "9876543210",
			}
		).insert(ignore_permissions=True)
		with (
			patch(f"{MODULE}.get_contact_by_phone_number", return_value={"lead": lead.name}),
			patch(f"{MODULE}._s3_has", return_value=True),
			patch(f"{MODULE}._upsert_call_log", side_effect=ValueError("test failure")),
		):
			row = migration._process_call(call(), MagicMock(), "bucket", "recordings", "old", False)
		self.assertEqual(row["result"], "error")
		self.assertTrue(frappe.db.exists("CRM Lead", lead.name))

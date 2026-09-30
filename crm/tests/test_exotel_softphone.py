from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from crm.integrations.exotel.handler import (
	ExotelDialOutcomeUnknown,
	_get_softphone_app_token,
	get_call_log_status,
	get_calls_api_call_log_status,
	get_softphone_agent_user,
	get_softphone_config,
	make_softphone_call,
	normalize_call_payload,
	normalize_exotel_datetime,
	reconcile_call_log,
	register_softphone_call,
	update_call_log,
)

# Shape of a real Integration Core `callback` captured on 2026-09-28 (identifiers replaced).
CAPTURED_OUTBOUND_TERMINAL = {
	"AccountDomain": "mumbai",
	"AppId": "app-id",
	"AppUserID": "agent@example.com",
	"CallDetail": "terminal",
	"CallRecordings": "https://recording.example/call.mp3",
	"CallSid": "call-sid",
	"CallState": "terminal",
	"CallStatus": "completed",
	"DialWhomNumber": "",
	"Direction": "outbound",
	"EndTime": "2026-09-28T14:31:29+05:30",
	"FromNumber": "sip:agentsip",
	"StartTime": "2026-09-28T14:31:08+05:30",
	"ToNumber": "09000000002",
	"TotalDuration": "10",
	"VirtualNumber": "+914000000001",
}

# Inbound Integration Core notification captured on 2026-09-29; identical shape for answered,
# missed and rejected calls apart from CallStatus ("busy" then "free").
CAPTURED_INBOUND_NOTIFICATION = {
	"AppId": "app-id",
	"AppUserID": "agent@example.com",
	"CallDetail": "",
	"CallRecordings": "",
	"CallSid": "inbound-sid",
	"CallState": "active",
	"CallStatus": "busy",
	"DialWhomNumber": "sip:agentsip",
	"Direction": "incoming",
	"EndTime": "",
	"FromNumber": "09000000001",
	"StartTime": "",
	"ToNumber": "04000000001",
	"TotalDuration": 0,
	"VirtualNumber": "04000000001",
}


def calls_api_inbound(leg2_status, conversation, recording=""):
	return {
		"Direction": "inbound",
		"Status": "completed",
		"RecordingUrl": recording,
		"Details": {
			"Leg1Status": "completed",
			"Leg2Status": leg2_status,
			"ConversationDuration": conversation,
		},
	}


def fake_call_log(**fields):
	call_log = frappe._dict(fields)
	call_log.save = MagicMock()
	call_log.set = lambda field, value: setattr(call_log, field, value)
	return call_log


class TestExotelSoftphone(FrappeTestCase):
	@patch("crm.integrations.exotel.handler.frappe.cache")
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_app_token_uses_proven_exotel_contract(self, mock_post, mock_cache):
		cache = MagicMock()
		cache.get_value.return_value = None
		mock_cache.return_value = cache
		response = MagicMock()
		response.json.return_value = {"Status": "Success", "Data": "app-token"}
		mock_post.return_value = response

		token = _get_softphone_app_token("app-id", "app-secret")

		self.assertEqual(token, "app-token")
		mock_post.assert_called_once_with(
			"https://integrationscore.mum1.exotel.com/v2/integrations/token",
			json={"Id": "app-id", "Secret": "app-secret", "Entity": "app"},
			timeout=10,
		)
		cache.set_value.assert_called_once_with(
			"crm:exotel:softphone-token:app-id",
			"app-token",
			expires_in_sec=3600,
		)

	@patch("crm.integrations.exotel.handler._get_current_softphone_agent", return_value=None)
	@patch("crm.integrations.exotel.handler.get_exotel_settings")
	def test_config_is_disabled_without_agent_opt_in(self, get_settings, _get_agent):
		get_settings.return_value = SimpleNamespace(enabled=1, softphone_enabled=1)

		self.assertEqual(get_softphone_config(), {"enabled": False})

	def test_normalizes_captured_webrtc_callback(self):
		payload = normalize_call_payload(CAPTURED_OUTBOUND_TERMINAL)

		self.assertEqual(payload.AgentEmail, "agent@example.com")
		self.assertEqual(payload.Status, "completed")
		self.assertEqual(payload.CallLogStatus, "Completed")
		self.assertEqual(payload.Direction, "outbound-dial")
		self.assertEqual(payload.CallFrom, "sip:agentsip")
		self.assertEqual(payload.To, "09000000002")
		self.assertEqual(payload.ConversationDuration, 10.0)
		self.assertEqual(payload.RecordingUrl, "https://recording.example/call.mp3")

	def test_outbound_customer_leg_unanswered_is_not_answered(self):
		# Captured 2026-09-30 for both a rejected and an unanswered outbound call.
		payload = normalize_call_payload(
			{**CAPTURED_OUTBOUND_TERMINAL, "CallStatus": "to_leg_unanswered", "TotalDuration": 0}
		)

		self.assertEqual(payload.CallLogStatus, "Call Not Answered")

	def test_normalizes_inbound_direction_and_keeps_exophone_as_to(self):
		for direction in ("inbound", "incoming"):
			payload = normalize_call_payload(
				dict(CAPTURED_OUTBOUND_TERMINAL, Direction=direction, FromNumber="09000000002")
			)
			self.assertEqual(payload.Direction, "incoming")
			self.assertEqual(payload.CallFrom, "09000000002")
			self.assertEqual(payload.To, "+914000000001")

	def test_classic_payload_passes_through_unchanged(self):
		classic = {"CallSid": "sid", "Direction": "outbound-api", "Status": "completed", "To": "9123456789"}

		payload = normalize_call_payload(classic)

		self.assertEqual(dict(payload), classic)
		self.assertEqual(get_call_log_status(payload, "outbound-api"), "Completed")

	def test_unknown_webrtc_status_maps_to_none(self):
		payload = normalize_call_payload(dict(CAPTURED_OUTBOUND_TERMINAL, CallStatus="mystery"))

		self.assertIsNone(get_call_log_status(payload, payload.Direction))

	def test_normalizes_active_webrtc_callback_as_in_progress(self):
		payload = normalize_call_payload({"CallState": "active", "CallStatus": "", "Direction": "outbound"})

		self.assertEqual(payload.Status, "in-progress")
		self.assertEqual(payload.Direction, "outbound-dial")

	def test_normalizes_webrtc_timestamp_for_database(self):
		value = normalize_exotel_datetime("2026-09-28T14:31:08+05:30")

		self.assertIsNone(value.tzinfo)
		self.assertEqual(str(value), "2026-09-28 14:31:08")

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_terminal_call_log_does_not_regress(self, commit):
		call_log = frappe._dict(status="Completed", duration=10)
		call_log.save = MagicMock()
		call_log.set = lambda field, value: setattr(call_log, field, value)

		result = update_call_log(
			{"Direction": "outbound-dial", "Status": "in-progress", "ConversationDuration": 0},
			call_log=call_log,
		)

		self.assertIsNone(result)
		call_log.save.assert_not_called()
		commit.assert_not_called()

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_duplicate_callback_does_not_save_again(self, commit):
		call_log = frappe._dict(status="Completed", duration=10.0)
		call_log.save = MagicMock()
		call_log.set = lambda field, value: setattr(call_log, field, value)

		result = update_call_log(
			{"Direction": "outbound-dial", "Status": "completed", "ConversationDuration": 10},
			call_log=call_log,
		)

		self.assertIsNone(result)
		call_log.save.assert_not_called()
		commit.assert_not_called()

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_terminal_call_log_accepts_late_enrichment(self, commit):
		call_log = fake_call_log(status="Completed", duration=0, recording_url=None, to="09000000002")

		result = update_call_log(
			frappe._dict(
				Direction="outbound-dial",
				CallLogStatus="Call Not Answered",
				ConversationDuration=10,
				RecordingUrl="https://recording.example/call.mp3",
				To="+914000000001",
			),
			call_log=call_log,
		)

		self.assertIs(result, call_log)
		self.assertEqual(call_log.status, "Completed")
		self.assertEqual(call_log.to, "09000000002")
		self.assertEqual(call_log.duration, 10)
		self.assertEqual(call_log.recording_url, "https://recording.example/call.mp3")
		call_log.save.assert_called_once()

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_unknown_status_on_update_keeps_current_status(self, commit):
		call_log = fake_call_log(status="In Progress", duration=0)

		update_call_log(
			frappe._dict(Direction="outbound-dial", CallLogStatus=None, ConversationDuration=5),
			call_log=call_log,
		)

		self.assertEqual(call_log.status, "In Progress")
		self.assertEqual(call_log.duration, 5)

	@patch("crm.integrations.exotel.handler.frappe.db.exists")
	@patch("crm.integrations.exotel.handler.frappe.db.get_value")
	def test_softphone_agent_user_requires_enabled_telephony_agent(self, get_value, exists):
		get_value.return_value = "agent@example.com"
		exists.return_value = None
		self.assertIsNone(get_softphone_agent_user("agent@example.com"))

		exists.return_value = "agent@example.com"
		self.assertEqual(get_softphone_agent_user("agent@example.com"), "agent@example.com")
		self.assertIsNone(get_softphone_agent_user(""))

	@patch("crm.integrations.exotel.handler.update_call_log")
	@patch("crm.integrations.exotel.handler.fetch_exotel_call")
	def test_reconcile_completes_log_from_calls_api(self, fetch_call, update):
		fetch_call.return_value = {
			"Sid": "call-sid",
			"Direction": "inbound",
			"Status": "completed",
			"Duration": 34,
			"StartTime": "2026-09-28 23:58:11",
			"EndTime": "2026-09-28 23:58:45",
			"RecordingUrl": "https://recording.example/call.mp3",
			"Details": {"ConversationDuration": 20},
		}

		reconcile_call_log("call-sid", frappe.utils.now_datetime())

		payload = update.call_args.args[0]
		self.assertEqual(payload.CallLogStatus, "Completed")
		self.assertEqual(payload.Direction, "incoming")
		self.assertEqual(payload.ConversationDuration, 20)
		self.assertEqual(payload.RecordingUrl, "https://recording.example/call.mp3")
		self.assertNotIn("To", payload)

	@patch("crm.integrations.exotel.handler.frappe.log_error")
	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	@patch("crm.integrations.exotel.handler.frappe.db.set_value")
	@patch("crm.integrations.exotel.handler.fetch_exotel_call", return_value=None)
	def test_reconcile_fails_missing_call_only_after_grace_period(self, _fetch, set_value, _commit, _log):
		now = frappe.utils.now_datetime()

		reconcile_call_log("call-sid", frappe.utils.add_to_date(now, minutes=-10))
		set_value.assert_not_called()

		reconcile_call_log("call-sid", frappe.utils.add_to_date(now, hours=-2))
		set_value.assert_called_once_with("CRM Call Log", "call-sid", "status", "Failed")

	def test_inbound_busy_notification_means_ringing_not_terminal_busy(self):
		payload = normalize_call_payload(CAPTURED_INBOUND_NOTIFICATION)

		self.assertEqual(payload.Direction, "incoming")
		self.assertEqual(payload.CallLogStatus, "Ringing")
		self.assertEqual(payload.CallFrom, "09000000001")

	def test_inbound_outcome_comes_from_agent_leg(self):
		# Captured 2026-09-29: missed, rejected and answered all report top-level "completed".
		self.assertEqual(
			get_calls_api_call_log_status(calls_api_inbound("no-answer", 0)), "Call Not Answered"
		)
		self.assertEqual(get_calls_api_call_log_status(calls_api_inbound("busy", 0)), "Busy")
		self.assertEqual(get_calls_api_call_log_status(calls_api_inbound(None, 0)), "Call Not Answered")
		self.assertEqual(
			get_calls_api_call_log_status(calls_api_inbound("completed", 12, "https://rec.example/a.mp3")),
			"Completed",
		)

	def test_outbound_outcome_uses_top_level_status(self):
		self.assertEqual(
			get_calls_api_call_log_status({"Direction": "outbound-dial", "Status": "no-answer"}),
			"Call Not Answered",
		)
		self.assertEqual(
			get_calls_api_call_log_status({"Direction": "outbound-dial", "Status": "completed"}), "Completed"
		)

	@patch("crm.integrations.exotel.handler.update_call_log")
	@patch("crm.integrations.exotel.handler.fetch_exotel_call")
	def test_reconcile_waits_until_exotel_finalises_call(self, fetch_call, update):
		# Call Details API populates EndTime/Duration/legs ~2 min after the call ends.
		fetch_call.return_value = dict(calls_api_inbound(None, 0), EndTime="")

		reconcile_call_log("call-sid", frappe.utils.now_datetime())

		update.assert_not_called()

	def test_flow_passthru_outcomes_for_inbound_calls(self):
		# Connect-applet Passthru payloads captured on 2026-09-29.
		missed = {"CallType": "incomplete", "DialCallStatus": "no-answer", "Direction": "incoming"}
		rejected = {"CallType": "incomplete", "DialCallStatus": "busy", "Direction": "incoming"}

		self.assertEqual(get_call_log_status(missed, "incoming"), "Call Not Answered")
		self.assertEqual(get_call_log_status(rejected, "incoming"), "Busy")

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_unanswered_passthru_does_not_store_ring_time_as_duration(self, commit):
		call_log = fake_call_log(status="Ringing", duration=None)

		update_call_log(
			frappe._dict(
				CallType="incomplete", DialCallStatus="busy", DialCallDuration="5", Direction="incoming"
			),
			call_log=call_log,
		)

		self.assertEqual(call_log.status, "Busy")
		self.assertFalse(call_log.duration)

	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	def test_answered_passthru_uses_talk_time_and_ignores_epoch_end_time(self, commit):
		# Answered-call Passthru captured on 2026-09-29.
		call_log = fake_call_log(status="Ringing", duration=None, end_time=None, recording_url=None)

		update_call_log(
			frappe._dict(
				{
					"CallType": "completed",
					"DialCallStatus": "completed",
					"DialCallDuration": "13",
					"Legs[0][OnCallDuration]": "11",
					"Direction": "incoming",
					"StartTime": "2026-09-29 02:00:31",
					"EndTime": "1970-01-01 05:30:00",
					"RecordingUrl": "https://recording.example/call.mp3",
				}
			),
			call_log=call_log,
		)

		self.assertEqual(call_log.status, "Completed")
		self.assertEqual(call_log.duration, "11")
		self.assertIsNone(call_log.end_time)
		self.assertEqual(call_log.recording_url, "https://recording.example/call.mp3")

	def test_inbound_to_is_exophone_not_agent_device(self):
		from crm.integrations.exotel.handler import get_call_log_to_number

		inbound_passthru = {"Direction": "incoming", "DialWhomNumber": "sip:agentsip", "To": "04000000001"}
		inbound_notification = normalize_call_payload(CAPTURED_INBOUND_NOTIFICATION)
		outbound = {"Direction": "outbound-api", "DialWhomNumber": "", "To": "09000000002"}

		self.assertEqual(get_call_log_to_number(inbound_passthru), "04000000001")
		self.assertEqual(get_call_log_to_number(inbound_notification), "04000000001")
		self.assertEqual(get_call_log_to_number(outbound), "09000000002")

	@patch("crm.integrations.exotel.handler.reconcile_call_log")
	@patch("crm.integrations.exotel.handler.frappe.get_all", return_value=[])
	def test_reconcile_includes_terminal_logs_missing_end_time(self, get_all, _reconcile):
		from crm.integrations.exotel.handler import _reconcile_stale_call_logs

		_reconcile_stale_call_logs()

		stale, awaiting_recording = (call.kwargs for call in get_all.call_args_list)
		self.assertEqual(stale["or_filters"]["end_time"], ["is", "not set"])
		self.assertEqual(stale["filters"]["status"], ["!=", "Failed"])
		# Completed calls stay eligible (for a bounded window) until Exotel publishes the recording.
		self.assertEqual(awaiting_recording["filters"]["status"], "Completed")
		self.assertEqual(awaiting_recording["filters"]["recording_url"], ["is", "not set"])

	@patch("crm.integrations.exotel.handler._schedule_next_reconcile")
	@patch("crm.integrations.exotel.handler._reconcile_due", return_value=True)
	@patch("crm.integrations.exotel.handler.reconcile_call_log")
	@patch("crm.integrations.exotel.handler.frappe.get_all")
	def test_reconcile_processes_each_log_once(self, get_all, reconcile, *_):
		from crm.integrations.exotel.handler import _reconcile_stale_call_logs

		log = frappe._dict(name="call-sid", creation=frappe.utils.now_datetime())
		get_all.side_effect = [[log], [log]]

		_reconcile_stale_call_logs()

		reconcile.assert_called_once_with("call-sid", log.creation)


def fake_response(status_code, body):
	response = MagicMock(status_code=status_code, text=str(body))
	response.json.return_value = body
	return response


SOFTPHONE_SETTINGS = SimpleNamespace(
	enabled=1,
	softphone_enabled=1,
	softphone_app_id="app-id",
	account_sid="acct",
	get_password=lambda field, raise_exception=True: "app-secret",
)
AGENT = frappe._dict(user="agent@example.com", mobile_no="+91 90000 00001", exotel_number="04000000001")


class TestExotelSoftphoneProvisioning(FrappeTestCase):
	@patch("crm.integrations.exotel.handler._get_softphone_app_token", return_value="app-token")
	@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SOFTPHONE_SETTINGS)
	@patch("crm.integrations.exotel.handler.requests.request")
	def test_existing_mapping_returns_sip_id_without_creating(self, request, _settings, _token):
		from crm.integrations.exotel.handler import ensure_softphone_user_mapping

		request.return_value = fake_response(200, {"Code": 200, "Data": {"SipId": "sip:agentsip"}})

		self.assertEqual(ensure_softphone_user_mapping(AGENT), "sip:agentsip")
		self.assertEqual(request.call_count, 1)
		self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "app-token"})

	@patch("crm.integrations.exotel.handler._get_softphone_app_token", return_value="app-token")
	@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SOFTPHONE_SETTINGS)
	@patch("crm.integrations.exotel.handler.requests.request")
	def test_missing_mapping_is_created_then_read(self, request, _settings, _token):
		from crm.integrations.exotel.handler import ensure_softphone_user_mapping

		request.side_effect = [
			# Integration Core reports "not found" as HTTP 200 with Code 404 in the body.
			fake_response(200, {"Code": 404, "Data": None}),
			fake_response(200, {"Code": 200, "Data": [{"AppUserId": "agent@example.com"}]}),
			fake_response(200, {"Code": 200, "Data": {"SipId": "sip:agentsip"}}),
		]

		self.assertEqual(ensure_softphone_user_mapping(AGENT), "sip:agentsip")
		body = request.call_args_list[1].kwargs["json"][0]
		self.assertEqual(body["AppUserId"], "agent@example.com")
		self.assertEqual(body["AgentNumber"], "9000000001")
		self.assertEqual(body["VirtualNumber"], "04000000001")

	@patch("crm.integrations.exotel.handler._get_softphone_app_token", return_value="app-token")
	@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SOFTPHONE_SETTINGS)
	@patch("crm.integrations.exotel.handler.requests.request")
	def test_mapping_without_sip_device_is_rejected(self, request, _settings, _token):
		from crm.integrations.exotel.handler import ensure_softphone_user_mapping

		request.return_value = fake_response(200, {"Code": 200, "Data": {"SipId": ""}})

		with self.assertRaises(frappe.ValidationError):
			ensure_softphone_user_mapping(AGENT)


class TestExotelSoftphoneCallVerification(FrappeTestCase):
	def _check(self, log, call, sip="sip:agentsip"):
		from crm.integrations.exotel.handler import softphone_call_matches_log

		def get_value(doctype, filters, fieldname=None, as_dict=False):
			return frappe._dict(log) if doctype == "CRM Call Log" else sip

		with patch("crm.integrations.exotel.handler.frappe.db.get_value", side_effect=get_value):
			return softphone_call_matches_log("call-sid", call)

	def test_outbound_matches_agent_sip_and_dialled_number_across_formats(self):
		log = {
			"type": "Outgoing",
			"caller": "agent@example.com",
			"to": "+919000000002",
			"is_softphone_call": 1,
		}
		self.assertTrue(self._check(log, {"From": "sip:agentsip", "To": "09000000002"}))

	def test_outbound_from_another_agents_sip_fails(self):
		log = {"type": "Outgoing", "caller": "agent@example.com", "to": "09000000002", "is_softphone_call": 1}
		self.assertFalse(self._check(log, {"From": "sip:someoneelse", "To": "09000000002"}))

	def test_outbound_to_a_different_number_fails(self):
		log = {"type": "Outgoing", "caller": "agent@example.com", "to": "09000000002", "is_softphone_call": 1}
		self.assertFalse(self._check(log, {"From": "sip:agentsip", "To": "09999999999"}))

	def test_inbound_must_ring_the_receivers_sip(self):
		log = {
			"type": "Incoming",
			"receiver": "agent@example.com",
			"to": "04000000001",
			"is_softphone_call": 1,
		}
		self.assertTrue(self._check(log, {"From": "09000000001", "To": "sip:agentsip"}))
		self.assertFalse(self._check(log, {"From": "09000000001", "To": "sip:someoneelse"}))

	def test_browser_log_pointing_at_a_non_sip_call_fails(self):
		# A spoofed CallSid of an unrelated click-to-call has phone-number legs only.
		log = {"type": "Outgoing", "caller": "agent@example.com", "to": "09000000002", "is_softphone_call": 1}
		self.assertFalse(self._check(log, {"From": "09000000001", "To": "09000000002"}))

	def test_browser_log_without_agent_sip_id_fails(self):
		log = {"type": "Outgoing", "caller": "agent@example.com", "to": "09000000002", "is_softphone_call": 1}
		self.assertFalse(self._check(log, {"From": "sip:agentsip", "To": "09000000002"}, sip=None))

	def test_server_created_logs_are_trusted(self):
		log = {"type": "Outgoing", "caller": "agent@example.com", "to": "09000000002", "is_softphone_call": 0}
		self.assertTrue(self._check(log, {"From": "09000000001", "To": "09000000002"}))


class TestExotelCallLogReference(FrappeTestCase):
	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	@patch("crm.integrations.exotel.handler.link")
	def test_unmatched_call_does_not_keep_default_reference_doctype(self, _link, _commit):
		from crm.integrations.exotel.handler import create_call_log

		# A real insert: Frappe re-applies DocType defaults on insert, which a mocked doc hides.
		create_call_log("test-unmatched-sid", "09000000001", "04000000001", "04000000001", "Administrator")

		self.assertFalse(frappe.db.get_value("CRM Call Log", "test-unmatched-sid", "reference_doctype"))


class TestExotelSoftphoneConcurrentInsert(FrappeTestCase):
	@patch("crm.integrations.exotel.handler._claim_existing_softphone_call")
	@patch("crm.integrations.exotel.handler.frappe.db.exists", return_value="call-sid")
	@patch("crm.integrations.exotel.handler.frappe.db.rollback")
	@patch("crm.integrations.exotel.handler.create_call_log", side_effect=frappe.QueryDeadlockError)
	def test_concurrent_insert_of_same_call_claims_existing_log(self, _create, _rollback, _exists, claim):
		from crm.integrations.exotel.handler import _create_softphone_call_log

		_create_softphone_call_log(SOFTPHONE_AGENT, "call-sid", "9123456789", "Incoming", None, None)

		claim.assert_called_once_with("call-sid")

	@patch("crm.integrations.exotel.handler.frappe.db.exists", return_value=None)
	@patch("crm.integrations.exotel.handler.frappe.db.rollback")
	@patch("crm.integrations.exotel.handler.create_call_log", side_effect=frappe.QueryDeadlockError)
	def test_real_deadlock_without_a_log_is_raised(self, *_):
		from crm.integrations.exotel.handler import _create_softphone_call_log

		with self.assertRaises(frappe.QueryDeadlockError):
			_create_softphone_call_log(SOFTPHONE_AGENT, "call-sid", "9123456789", "Incoming", None, None)


@patch("crm.integrations.exotel.handler._schedule_next_reconcile")
@patch("crm.integrations.exotel.handler.reconcile_call_log")
@patch("crm.integrations.exotel.handler.frappe.get_all")
class TestExotelReconcileBudget(FrappeTestCase):
	def test_status_repairs_and_recordings_have_separate_budgets(self, get_all, reconcile, _schedule):
		from crm.integrations.exotel.handler import (
			RECONCILE_BATCH_SIZE,
			RECONCILE_RECORDING_BATCH_SIZE,
			_reconcile_stale_call_logs,
		)

		now = frappe.utils.now_datetime()
		stale = [frappe._dict(name=f"stale-{i}", creation=now) for i in range(RECONCILE_BATCH_SIZE * 2)]
		awaiting = [frappe._dict(name=f"rec-{i}", creation=now) for i in range(RECONCILE_BATCH_SIZE)]
		get_all.side_effect = [stale, awaiting]

		with patch("crm.integrations.exotel.handler._reconcile_due", return_value=True):
			_reconcile_stale_call_logs()

		called = [c.args[0] for c in reconcile.call_args_list]
		self.assertEqual(sum(name.startswith("stale-") for name in called), RECONCILE_BATCH_SIZE)
		self.assertEqual(sum(name.startswith("rec-") for name in called), RECONCILE_RECORDING_BATCH_SIZE)

	def test_logs_waiting_on_backoff_dont_block_newer_ones(self, get_all, reconcile, schedule):
		from crm.integrations.exotel.handler import RECONCILE_BATCH_SIZE, _reconcile_stale_call_logs

		now = frappe.utils.now_datetime()
		stuck = [frappe._dict(name=f"stuck-{i}", creation=now) for i in range(RECONCILE_BATCH_SIZE)]
		fresh = [frappe._dict(name="fresh", creation=now)]
		get_all.side_effect = [stuck + fresh, []]

		with patch(
			"crm.integrations.exotel.handler._reconcile_due",
			side_effect=lambda sid: not sid.startswith("stuck"),
		):
			_reconcile_stale_call_logs()

		reconcile.assert_called_once_with("fresh", now)
		schedule.assert_called_once_with("fresh")


class TestExotelReconcileBackoff(FrappeTestCase):
	def setUp(self):
		frappe.cache.delete_value("crm:exotel:reconcile-backoff:backoff-sid")

	def tearDown(self):
		frappe.cache.delete_value("crm:exotel:reconcile-backoff:backoff-sid")

	def test_retries_back_off_and_are_capped(self):
		from crm.integrations.exotel.handler import (
			RECONCILE_MAX_BACKOFF_MINUTES,
			_reconcile_due,
			_schedule_next_reconcile,
		)

		self.assertTrue(_reconcile_due("backoff-sid"))
		_schedule_next_reconcile("backoff-sid")
		self.assertFalse(_reconcile_due("backoff-sid"))

		start = frappe.utils.now_datetime().timestamp()
		for _attempt in range(20):
			_schedule_next_reconcile("backoff-sid")
		state = frappe.cache.get_value("crm:exotel:reconcile-backoff:backoff-sid", expires=True)
		self.assertEqual(state["attempts"], 21)
		self.assertLessEqual(state["next_at"] - start, RECONCILE_MAX_BACKOFF_MINUTES * 60 + 5)


class TestExotelSoftphoneClaimExistingLog(FrappeTestCase):
	def _register(self, existing_log):
		call_log = frappe._dict(existing_log)
		settings = SimpleNamespace(enabled=1, softphone_enabled=1)
		agent = frappe._dict(
			user=frappe.session.user,
			mobile_no="9876543210",
			exotel_number="0112345678",
			exotel_sip_id="sip:x",
		)
		with (
			patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=settings),
			patch("crm.integrations.exotel.handler._get_current_softphone_agent", return_value=agent),
			patch("crm.integrations.exotel.handler.frappe.db.exists", return_value="call-sid"),
			patch("crm.integrations.exotel.handler.frappe.get_doc", return_value=call_log),
			patch("crm.integrations.exotel.handler.frappe.db.set_value") as set_value,
		):
			register_softphone_call("call-sid", "9123456789", "Incoming")
		return set_value

	def test_webhook_created_log_is_flagged_when_its_agent_registers_it(self):
		set_value = self._register({"caller": None, "receiver": frappe.session.user})

		set_value.assert_called_once_with(
			"CRM Call Log", "call-sid", "is_softphone_call", 1, update_modified=False
		)

	def test_log_without_handler_is_not_flagged(self):
		set_value = self._register({"caller": None, "receiver": None})

		set_value.assert_not_called()

	def test_other_agents_log_is_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			self._register({"caller": None, "receiver": "someone-else@example.com"})


SOFTPHONE_AGENT = frappe._dict(
	user="agent@example.com",
	mobile_no="9876543210",
	exotel_number="04000000001",
	exotel_sip_id="sip:agentsip",
)
USER_MAPPING = {
	"CustomerId": "customer",
	"AppID": "app-id",
	"AppUserId": "agent@example.com",
	"ExotelAccountSid": "acct",
	"ExotelUserName": "Agent",
	"AgentNumber": "9000000001",
	"VirtualNumber": "04000000001",
	"SipId": "sip:agentsip",
	"SipSecret": "encrypted-secret",
}
DIAL_SUCCESS = {
	"Status": "Success",
	"Code": 200,
	"Data": {"CallSid": "call-sid", "AppUserID": "agent@example.com", "FromNumber": "sip:agentsip"},
}


@patch("crm.integrations.exotel.handler._get_softphone_app_token", return_value="app-token")
@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SOFTPHONE_SETTINGS)
@patch("crm.integrations.exotel.handler._get_current_softphone_agent", return_value=SOFTPHONE_AGENT)
@patch("crm.integrations.exotel.handler._get_agent_email", return_value="agent@example.com")
class TestExotelSoftphoneServerSideToken(FrappeTestCase):
	@patch("crm.integrations.exotel.handler.requests.request")
	def test_config_returns_only_the_agents_own_sip_mapping(self, request, *_):
		request.return_value = fake_response(200, {"Code": 200, "Data": USER_MAPPING})

		config = get_softphone_config()

		self.assertEqual(
			config,
			{
				"enabled": True,
				"AppID": "app-id",
				"AppUserId": "agent@example.com",
				"SipId": "sip:agentsip",
				"SipSecret": "encrypted-secret",
				"ExotelUserName": "Agent",
				"ExotelAccountSid": "acct",
			},
		)
		self.assertNotIn("app-token", str(config))
		self.assertEqual(request.call_args.kwargs["params"], {"user_id": "agent@example.com"})

	@patch("crm.integrations.exotel.handler.requests.request")
	def test_config_rejects_a_mapping_for_another_sip_device(self, request, *_):
		request.return_value = fake_response(
			200, {"Code": 200, "Data": {**USER_MAPPING, "SipId": "sip:other"}}
		)

		with self.assertRaises(frappe.ValidationError):
			get_softphone_config()

	@patch("crm.integrations.exotel.handler.requests.request")
	def test_config_rejects_an_unmapped_agent(self, request, *_):
		request.return_value = fake_response(200, {"Code": 404, "Data": None})

		with self.assertRaises(frappe.ValidationError):
			get_softphone_config()

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log")
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_dial_uses_server_token_and_session_agent(self, post, create_log, *_):
		post.return_value = fake_response(200, DIAL_SUCCESS)
		post.return_value.ok = True

		with patch("crm.integrations.exotel.handler._validate_softphone_reference") as validate_reference:
			result = make_softphone_call("+91 91234 56789", "CRM Lead", "LEAD-1")

		self.assertEqual(result, {"CallSid": "call-sid"})
		validate_reference.assert_called_once_with("CRM Lead", "LEAD-1")
		self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "app-token"})
		self.assertEqual(
			post.call_args.kwargs["json"],
			{"app_id": "app-id", "to": "+91 91234 56789", "user_id": "agent@example.com"},
		)
		create_log.assert_called_once_with(
			SOFTPHONE_AGENT, "call-sid", "+91 91234 56789", "Outgoing", "CRM Lead", "LEAD-1"
		)

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log")
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_rejected_dial_is_a_definite_failure(self, post, create_log, *_):
		post.return_value = fake_response(400, {"Status": "Failure", "Code": 400, "Error": "bad number"})
		post.return_value.ok = False

		with self.assertRaises(frappe.ValidationError) as raised:
			make_softphone_call("9123456789")

		self.assertNotIsInstance(raised.exception, ExotelDialOutcomeUnknown)
		create_log.assert_not_called()

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log")
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_timeout_or_server_error_means_outcome_unknown(self, post, create_log, *_):
		for outcome in (requests.ReadTimeout(), requests.ConnectionError(), fake_response(502, {})):
			post.reset_mock()
			post.side_effect = outcome if isinstance(outcome, Exception) else None
			post.return_value = outcome
			with self.subTest(outcome=outcome), self.assertRaises(ExotelDialOutcomeUnknown):
				make_softphone_call("9123456789")
		create_log.assert_not_called()

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log")
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_dial_ringing_another_sip_device_is_outcome_unknown(self, post, create_log, *_):
		post.return_value = fake_response(
			200, {**DIAL_SUCCESS, "Data": {**DIAL_SUCCESS["Data"], "FromNumber": "sip:other"}}
		)
		post.return_value.ok = True

		# Exotel accepted the dial, so the browser must treat stray rings as possibly this call.
		with self.assertRaises(ExotelDialOutcomeUnknown):
			make_softphone_call("9123456789")
		create_log.assert_not_called()

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler.frappe.db.rollback")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log", side_effect=Exception("db down"))
	@patch("crm.integrations.exotel.handler.requests.post")
	def test_log_failure_after_exotel_accepts_still_returns_the_call(self, post, _create_log, rollback, *_):
		post.return_value = fake_response(200, DIAL_SUCCESS)
		post.return_value.ok = True

		self.assertEqual(make_softphone_call("9123456789"), {"CallSid": "call-sid"})
		rollback.assert_called_once()

	@patch("crm.integrations.exotel.handler._check_softphone_dial_rate")
	@patch("crm.integrations.exotel.handler._create_softphone_call_log")
	@patch("crm.integrations.exotel.handler.requests.post", side_effect=requests.ConnectTimeout())
	def test_connect_timeout_is_a_definite_failure(self, _post, create_log, *_):
		with self.assertRaises(frappe.ValidationError) as raised:
			make_softphone_call("9123456789")

		self.assertNotIsInstance(raised.exception, ExotelDialOutcomeUnknown)
		create_log.assert_not_called()

	@patch("crm.integrations.exotel.handler.requests.post")
	def test_invalid_number_is_rejected_before_dialling(self, post, *_):
		with self.assertRaises(frappe.ValidationError):
			make_softphone_call("12345")
		post.assert_not_called()

	def test_dials_are_limited_per_user(self, *_):
		from crm.integrations.exotel.handler import SOFTPHONE_DIALS_PER_MINUTE, _check_softphone_dial_rate

		frappe.cache.delete_value(f"crm:exotel:softphone-dials:{frappe.session.user}")
		try:
			for _dial in range(SOFTPHONE_DIALS_PER_MINUTE):
				_check_softphone_dial_rate()
			with self.assertRaises(frappe.RateLimitExceededError):
				_check_softphone_dial_rate()
		finally:
			frappe.cache.delete_value(f"crm:exotel:softphone-dials:{frappe.session.user}")

	def test_browser_can_no_longer_register_outgoing_calls(self, *_):
		with self.assertRaises(frappe.ValidationError):
			register_softphone_call("call-sid", "9123456789", "Outgoing")


class TestExotelFreeNotificationReconcile(FrappeTestCase):
	@patch("crm.integrations.exotel.handler.frappe.enqueue")
	@patch("crm.integrations.exotel.handler.frappe.db.exists", return_value=True)
	@patch("crm.integrations.exotel.handler.get_softphone_agent_user", return_value="agent@example.com")
	@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SimpleNamespace(enabled=1))
	@patch("crm.integrations.exotel.handler.create_request_log")
	@patch("crm.integrations.exotel.handler.is_integration_enabled", return_value=True)
	@patch("crm.integrations.exotel.handler.validate_request")
	@patch("crm.integrations.exotel.handler.frappe.request", new=MagicMock())
	def test_free_notification_enqueues_a_callable_reconcile_job(self, *mocks):
		import inspect

		from frappe.utils.background_jobs import enqueue as real_enqueue

		from crm.integrations.exotel.handler import handle_request, reconcile_call_log

		enqueue = mocks[-1]
		handle_request(**{**CAPTURED_INBOUND_NOTIFICATION, "CallStatus": "free"})

		enqueue.assert_called_once()
		job_kwargs = {
			key: value
			for key, value in enqueue.call_args.kwargs.items()
			if key not in inspect.signature(real_enqueue).parameters
		}
		# The worker calls the job with every non-enqueue kwarg; an extra one (e.g. user=) raises TypeError.
		inspect.signature(reconcile_call_log).bind(**job_kwargs)


@patch("crm.integrations.exotel.handler.get_exotel_settings", return_value=SOFTPHONE_SETTINGS)
@patch("crm.integrations.exotel.handler._get_current_softphone_agent", return_value=SOFTPHONE_AGENT)
@patch("crm.integrations.exotel.handler.frappe.db.exists", return_value=None)
@patch("crm.integrations.exotel.handler._create_softphone_call_log")
class TestExotelInboundCallerNumber(FrappeTestCase):
	@patch(
		"crm.integrations.exotel.handler.fetch_exotel_call",
		return_value={"Direction": "inbound", "From": "09000000002", "To": "sip:agentsip"},
	)
	def test_registration_uses_exotels_caller_not_the_browsers(self, fetch, create_log, *_):
		register_softphone_call("call-sid", "sipuser-junk", "Incoming")

		self.assertEqual(fetch.call_args.kwargs["timeout"], 3)
		self.assertEqual(create_log.call_args.args[2], "09000000002")

	@patch("crm.integrations.exotel.handler.fetch_exotel_call", side_effect=requests.ReadTimeout())
	def test_registration_falls_back_to_the_browser_number(self, _fetch, create_log, *_):
		register_softphone_call("call-sid", "09000000002", "Incoming")

		self.assertEqual(create_log.call_args.args[2], "09000000002")


class TestExotelInboundCallerCorrection(FrappeTestCase):
	@patch("crm.integrations.exotel.handler.frappe.db.commit")
	@patch("crm.integrations.exotel.handler.frappe.log_error")
	@patch("crm.integrations.exotel.handler.link")
	@patch("crm.integrations.exotel.handler.frappe.get_doc")
	@patch("crm.integrations.exotel.handler.frappe.db.get_value")
	def test_reconcile_replaces_a_wrong_browser_number_and_relinks(
		self, get_value, get_doc, link, log_error, _commit
	):
		from crm.integrations.exotel.handler import correct_inbound_caller

		get_value.return_value = frappe._dict(type="Incoming", is_softphone_call=1, **{"from": "09999999999"})
		call_log = MagicMock()
		call_log.get.return_value = "09999999999"
		get_doc.return_value = call_log

		correct_inbound_caller("call-sid", {"From": "09000000002"})

		call_log.set.assert_any_call("from", "09000000002")
		call_log.set.assert_any_call("links", [])
		link.assert_called_once_with("09000000002", call_log)
		call_log.save.assert_called_once_with(ignore_permissions=True)
		log_error.assert_called_once()

	@patch("crm.integrations.exotel.handler.frappe.get_doc")
	@patch("crm.integrations.exotel.handler.frappe.db.get_value")
	def test_matching_or_server_created_logs_are_left_alone(self, get_value, get_doc):
		from crm.integrations.exotel.handler import correct_inbound_caller

		for log in (
			frappe._dict(type="Incoming", is_softphone_call=1, **{"from": "+91 90000 00002"}),
			frappe._dict(type="Incoming", is_softphone_call=0, **{"from": "09999999999"}),
			frappe._dict(type="Outgoing", is_softphone_call=1, **{"from": "09999999999"}),
		):
			get_value.return_value = log
			correct_inbound_caller("call-sid", {"From": "09000000002"})
		get_doc.assert_not_called()

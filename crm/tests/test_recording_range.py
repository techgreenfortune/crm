from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from crm.integrations.api import get_recording_url

AUDIO = bytes(range(256)) * 4


class TestRecordingRange(FrappeTestCase):
	def _fetch(self, headers=None):
		upstream = MagicMock(content=AUDIO)
		upstream.__enter__.return_value = upstream
		request = Request(EnvironBuilder(path="/", headers=headers or {}).get_environ())
		with (
			patch("crm.integrations.api.frappe.db.exists", return_value=True),
			patch(
				"crm.integrations.api.frappe.get_doc",
				return_value=frappe._dict(
					recording_url="https://recordings.example/a.mp3", telephony_medium="Exotel"
				),
			),
			patch("crm.integrations.api._get_recording_credentials", return_value=None),
			patch("crm.integrations.api.requests.get", return_value=upstream),
			patch.object(frappe.local, "request", request, create=True),
		):
			return get_recording_url("call-log")

	def test_range_request_returns_partial_content(self):
		response = self._fetch({"Range": "bytes=100-199"})

		self.assertEqual(response.status_code, 206)
		self.assertEqual(response.get_data(), AUDIO[100:200])
		self.assertEqual(response.headers["Content-Range"], f"bytes 100-199/{len(AUDIO)}")

	def test_plain_request_returns_the_whole_file_and_advertises_ranges(self):
		response = self._fetch()

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.get_data(), AUDIO)
		self.assertEqual(response.headers["Accept-Ranges"], "bytes")
		self.assertEqual(response.mimetype, "audio/mpeg")

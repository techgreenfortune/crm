from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from crm.integrations.api import get_recording_url

AUDIO = bytes(range(256)) * 4


class TestRecordingRange(FrappeTestCase):
	def _fetch(self, headers=None, upstream=None):
		if upstream is None:
			# A provider that ignores Range and always sends the whole file.
			upstream = MagicMock(content=AUDIO, status_code=200, headers={})
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
			patch("crm.integrations.api.requests.get", return_value=upstream) as get,
			patch.object(frappe.local, "request", request, create=True),
		):
			self.upstream_get = get
			return get_recording_url("call-log")

	def test_range_is_forwarded_to_a_provider_that_supports_it(self):
		partial = MagicMock(
			content=AUDIO[100:200], status_code=206, headers={"Content-Range": f"bytes 100-199/{len(AUDIO)}"}
		)
		response = self._fetch({"Range": "bytes=100-199"}, upstream=partial)

		self.assertEqual(self.upstream_get.call_args.kwargs["headers"], {"Range": "bytes=100-199"})
		self.assertEqual(response.status_code, 206)
		self.assertEqual(response.get_data(), AUDIO[100:200])
		self.assertEqual(response.headers["Content-Range"], f"bytes 100-199/{len(AUDIO)}")

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

	def test_range_past_the_end_is_passed_through_as_416(self):
		unsatisfiable = MagicMock(
			content=b"", status_code=416, headers={"Content-Range": f"bytes */{len(AUDIO)}"}
		)
		response = self._fetch({"Range": f"bytes={len(AUDIO)}-"}, upstream=unsatisfiable)

		unsatisfiable.raise_for_status.assert_not_called()
		self.assertEqual(response.status_code, 416)
		self.assertEqual(response.headers["Content-Range"], f"bytes */{len(AUDIO)}")

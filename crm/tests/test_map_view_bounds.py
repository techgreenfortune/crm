from __future__ import annotations

from frappe.tests.utils import FrappeTestCase

from crm.api.doc import get_data
from crm.tests.factories import make_lead


class TestMapViewBounds(FrappeTestCase):
	def _map_view(self, map_bounds=None):
		return get_data(
			doctype="CRM Lead",
			filters={},
			order_by="modified desc",
			page_length=20,
			view={"view_type": "map"},
			map_bounds=map_bounds,
		)

	def test_default_load_excludes_leads_without_coordinates(self):
		without_coords = make_lead(phone="+919800091001")
		with_coords = make_lead(phone="+919800091002", custom_latitude=12.9716, custom_longitude=77.5946)

		names = {row["name"] for row in self._map_view()["data"]}

		self.assertIn(with_coords.name, names)
		self.assertNotIn(without_coords.name, names)

	def test_map_bounds_scopes_to_viewport(self):
		bengaluru = make_lead(phone="+919800091003", custom_latitude=12.9716, custom_longitude=77.5946)
		mumbai = make_lead(phone="+919800091004", custom_latitude=19.0760, custom_longitude=72.8777)

		names = {
			row["name"]
			for row in self._map_view(
				map_bounds={"min_lat": 12.0, "max_lat": 13.5, "min_lng": 77.0, "max_lng": 78.0}
			)["data"]
		}

		self.assertIn(bengaluru.name, names)
		self.assertNotIn(mumbai.name, names)

	def test_map_bounds_empty_viewport_returns_no_rows(self):
		make_lead(phone="+919800091005", custom_latitude=12.9716, custom_longitude=77.5946)

		result = self._map_view(
			map_bounds={"min_lat": 30.0, "max_lat": 31.0, "min_lng": 70.0, "max_lng": 71.0}
		)

		self.assertEqual(result["data"], [])
		self.assertEqual(result["total_count"], 0)

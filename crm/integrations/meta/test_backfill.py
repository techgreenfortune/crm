# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Pure-logic tests for the Meta Audience backfill script's batching.

The Won-skip / DB-reading parts were verified with a live dry run against
the dev site's real CRM Lead/Deal data (see developer notes) rather than
fixtures here, to avoid mutating that data; this covers the chunking
boundary cases only.
"""

from __future__ import annotations

from frappe.tests.utils import FrappeTestCase

from crm.integrations.meta.backfill import _chunks


class TestChunks(FrappeTestCase):
	def test_splits_into_even_batches(self):
		items = list(range(10))
		self.assertEqual(list(_chunks(items, 3)), [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])

	def test_batch_size_larger_than_items_returns_one_batch(self):
		items = list(range(5))
		self.assertEqual(list(_chunks(items, 10000)), [items])

	def test_empty_items_yields_no_batches(self):
		self.assertEqual(list(_chunks([], 100)), [])

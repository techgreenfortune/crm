"""
Merge IndiFrame Spotting + GreenFortune Spotting → Lead Spotting.

Run order:
    1. bench --site crm.indiframe.com execute crm.scripts.merge_sources.merge_spotting_sources
    2. bench --site crm.indiframe.com execute crm.scripts.merge_sources.delete_old_source_fixtures
    3. Edit fixtures (see instructions printed by step 2).
"""

import frappe


def merge_spotting_sources():
	"""
	Bulk-update CRM Lead (and CRM Deal) rows. Passes run A→B→C→D in order —
	specific sub-source maps first, catch-all last. Uses raw SQL to bypass
	Frappe hook stack (validate, before_save) for speed and to avoid
	sub-source mandatory validators.
	"""
	frappe.db.sql("SET @old_safe = @@SQL_SAFE_UPDATES, SQL_SAFE_UPDATES = 0")

	# A. IndiFrame Spotting + Lead Spotting - Projects → Lead Spotting + IndiFrame Projects
	frappe.db.sql(
		"""
		UPDATE `tabCRM Lead`
		SET source = 'Lead Spotting', custom_sub_source = 'IndiFrame Projects'
		WHERE source = 'IndiFrame Spotting' AND custom_sub_source = 'Lead Spotting - Projects'
		"""
	)
	count_a = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	print(f"[A] IndiFrame Spotting + Projects → Lead Spotting + IndiFrame Projects: {count_a}")

	# B. IndiFrame Spotting + Lead Spotting - Retail → Lead Spotting + IndiFrame Retail
	frappe.db.sql(
		"""
		UPDATE `tabCRM Lead`
		SET source = 'Lead Spotting', custom_sub_source = 'IndiFrame Retail'
		WHERE source = 'IndiFrame Spotting' AND custom_sub_source = 'Lead Spotting - Retail'
		"""
	)
	count_b = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	print(f"[B] IndiFrame Spotting + Retail   → Lead Spotting + IndiFrame Retail:  {count_b}")

	# C. IndiFrame Spotting (remaining) → Lead Spotting + NULL
	frappe.db.sql(
		"""
		UPDATE `tabCRM Lead`
		SET source = 'Lead Spotting', custom_sub_source = NULL
		WHERE source = 'IndiFrame Spotting'
		"""
	)
	count_c = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	print(f"[C] IndiFrame Spotting (other)    → Lead Spotting + NULL:              {count_c}")

	# D. GreenFortune Spotting → Lead Spotting + NULL
	frappe.db.sql(
		"""
		UPDATE `tabCRM Lead`
		SET source = 'Lead Spotting', custom_sub_source = NULL
		WHERE source = 'GreenFortune Spotting'
		"""
	)
	count_d = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	print(f"[D] GreenFortune Spotting         → Lead Spotting + NULL:              {count_d}")

	# E. CRM Deal (inherits source from lead at conversion)
	frappe.db.sql(
		"""
		UPDATE `tabCRM Deal`
		SET source = 'Lead Spotting'
		WHERE source IN ('IndiFrame Spotting', 'GreenFortune Spotting')
		"""
	)
	count_e = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	if count_e:
		print(f"[E] CRM Deal rows updated:                                          {count_e}")

	frappe.db.sql("SET SQL_SAFE_UPDATES = @old_safe")
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	print(f"\nTotal CRM Lead rows updated: {count_a + count_b + count_c + count_d}")
	print("Committed. Run delete_old_source_fixtures() next.")


def delete_old_source_fixtures():
	"""
	Delete deprecated CRM Lead Source and CRM Sub Source records.
	merge_spotting_sources() must have run first.
	"""
	old_sources = ["IndiFrame Spotting", "GreenFortune Spotting"]
	old_sub_sources = ["Lead Spotting - Projects", "Lead Spotting - Retail"]

	still_linked = frappe.db.count("CRM Lead", {"source": ["in", old_sources]})
	deal_linked = frappe.db.count("CRM Deal", {"source": ["in", old_sources]})
	sub_still_linked = frappe.db.count("CRM Lead", {"custom_sub_source": ["in", old_sub_sources]})
	if still_linked or deal_linked or sub_still_linked:
		print(
			f"ERROR: leads={still_linked} deals={deal_linked}"
			f" sub_source={sub_still_linked} still reference old values."
			" Run merge_spotting_sources() first."
		)
		return

	for name in old_sources:
		if frappe.db.exists("CRM Lead Source", name):
			frappe.delete_doc("CRM Lead Source", name, force=True, ignore_permissions=True)
			print(f"Deleted CRM Lead Source: {name!r}")
		else:
			print(f"CRM Lead Source {name!r} not found — already gone")

	for name in old_sub_sources:
		if frappe.db.exists("CRM Sub Source", name):
			frappe.delete_doc("CRM Sub Source", name, force=True, ignore_permissions=True)
			print(f"Deleted CRM Sub Source:  {name!r}")
		else:
			print(f"CRM Sub Source {name!r} not found — already gone")

	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	print(
		"\nDone. Now edit fixtures in git and commit:"
		"\n  crm/fixtures/crm_lead_source.json — remove IndiFrame Spotting, GreenFortune Spotting"
		"\n  crm/fixtures/crm_sub_source.json  — remove Lead Spotting - Projects, Lead Spotting - Retail"
	)

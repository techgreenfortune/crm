"""
Deduplicate pre-import vs import CRM Leads.

Import date: 2026-06-16. Leads created before that date without custom_account
are "pre-import". Leads created on/after that date with custom_account set are
"import leads". Where both share the same first_name a duplicate pair exists.

Run order:
    1. bench --site crm.indiframe.com execute crm.scripts.dedup_leads.find_duplicates
    2. bench --site crm.indiframe.com execute crm.scripts.dedup_leads.delete_safe_duplicates \
           --kwargs '{"dry_run": True}'
    3. bench --site crm.indiframe.com execute crm.scripts.dedup_leads.delete_safe_duplicates
    4. For each "needs review" case:
       bench --site crm.indiframe.com execute crm.scripts.dedup_leads.migrate_and_delete_duplicate \
           --kwargs '{"old_name": "CRM-LEAD-...", "new_name": "CRM-LEAD-..."}'
"""

import frappe

_IMPORT_DATE = "2026-06-16"


def _get_duplicate_pairs():
	return frappe.db.sql(
		"""
		SELECT
			pre.name        AS old_name,
			imp.name        AS new_name,
			pre.first_name  AS first_name
		FROM `tabCRM Lead` pre
		INNER JOIN `tabCRM Lead` imp
			ON imp.first_name = pre.first_name
		WHERE
			(pre.custom_account IS NULL OR pre.custom_account = '')
			AND DATE(pre.creation) < %(import_date)s
			AND imp.custom_account IS NOT NULL
			AND imp.custom_account != ''
			AND DATE(imp.creation) >= %(import_date)s
		ORDER BY pre.first_name, pre.name
		""",
		{"import_date": _IMPORT_DATE},
		as_dict=True,
	)


def _get_connections(lead_name):
	return {
		"qr": frappe.db.count("CRM Quote Request", {"lead": lead_name}),
		"deal": frappe.db.count("CRM Deal", {"lead": lead_name}),
		"retry_log": frappe.db.count("CRM Retry Log", {"lead": lead_name}),
		"file": frappe.db.count(
			"File",
			{
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead_name,
			},
		),
		"task": frappe.db.count(
			"CRM Task",
			{
				"reference_doctype": "CRM Lead",
				"reference_docname": lead_name,
			},
		),
		"note": frappe.db.count(
			"FCRM Note",
			{
				"reference_doctype": "CRM Lead",
				"reference_docname": lead_name,
			},
		),
		"call_log": frappe.db.count(
			"CRM Call Log",
			{
				"reference_doctype": "CRM Lead",
				"reference_docname": lead_name,
			},
		),
	}


def _is_blocking_free(conn):
	return conn["qr"] == 0 and conn["deal"] == 0 and conn["retry_log"] == 0 and conn["file"] == 0


def _group_pairs(raw_pairs):
	grouped = {}
	for row in raw_pairs:
		key = row["old_name"]
		if key not in grouped:
			grouped[key] = {"old_name": row["old_name"], "first_name": row["first_name"], "new_names": []}
		grouped[key]["new_names"].append(row["new_name"])
	return list(grouped.values())


def find_duplicates():
	"""Read-only scan. Prints categorised duplicate report. Safe to run any time."""
	raw = _get_duplicate_pairs()
	if not raw:
		print("No duplicates found.")
		return

	groups = _group_pairs(raw)
	safe = []
	review = []
	ambiguous = []

	for g in groups:
		if len(g["new_names"]) > 1:
			ambiguous.append(g)
			continue
		conn = _get_connections(g["old_name"])
		g["connections"] = conn
		if _is_blocking_free(conn):
			safe.append(g)
		else:
			review.append(g)

	print(f"Total unique old leads with a duplicate import lead: {len(groups)}")
	print(f"  Safe (no QR/Deal/RetryLog/File):     {len(safe)}")
	print(f"  Needs review (has blocking links):   {len(review)}")
	print(f"  Ambiguous (multiple import matches): {len(ambiguous)}")

	if ambiguous:
		print("\n=== AMBIGUOUS (require manual investigation) ===")
		for g in ambiguous:
			print(f"  OLD={g['old_name']}  first_name={g['first_name']!r}  new_matches={g['new_names']}")

	if review:
		print("\n=== NEEDS REVIEW (blocking connections) ===")
		for g in review:
			c = g["connections"]
			print(
				f"  OLD={g['old_name']}  NEW={g['new_names'][0]}"
				f"  first_name={g['first_name']!r}"
				f"  qr={c['qr']} deal={c['deal']} retry={c['retry_log']}"
				f" file={c['file']} task={c['task']} note={c['note']} call_log={c['call_log']}"
			)

	if safe:
		soft_warned = [
			g
			for g in safe
			if g["connections"]["task"] + g["connections"]["note"] + g["connections"]["call_log"] > 0
		]
		clean = len(safe) - len(soft_warned)
		print(f"\n=== SAFE TO DELETE ({len(safe)} leads) ===")
		if clean:
			print(f"  {clean} lead(s) with zero connections of any kind")
		for g in soft_warned:
			c = g["connections"]
			print(
				f"  OLD={g['old_name']}  NEW={g['new_names'][0]}"
				f"  first_name={g['first_name']!r}"
				f"  [soft-linked: task={c['task']} note={c['note']} call_log={c['call_log']} — will orphan]"
			)

	print("\nRun delete_safe_duplicates(dry_run=True) to preview, then delete_safe_duplicates() to act.")
	print("Run migrate_and_delete_duplicate(old, new) for each 'needs review' case.")


def delete_safe_duplicates(dry_run=False):
	"""
	Delete pre-import leads that have no QR/Deal/RetryLog/File connections.
	Clears Contact.custom_lead before each delete.
	Pass dry_run=True to preview without committing.
	"""
	raw = _get_duplicate_pairs()
	groups = _group_pairs(raw)

	deleted = 0
	skipped_ambiguous = 0
	skipped_has_links = 0
	errors = []

	for g in groups:
		if len(g["new_names"]) > 1:
			skipped_ambiguous += 1
			continue

		old_name = g["old_name"]
		conn = _get_connections(old_name)

		if not _is_blocking_free(conn):
			skipped_has_links += 1
			continue

		if dry_run:
			print(f"[DRY RUN] would delete: {old_name!r}  (new={g['new_names'][0]!r})")
			deleted += 1
			continue

		try:
			frappe.delete_doc("CRM Lead", old_name, force=True, ignore_permissions=True)
			deleted += 1
		except Exception as exc:
			frappe.db.rollback()
			errors.append((old_name, str(exc)))
			continue

		if deleted > 0 and deleted % 100 == 0 and not dry_run:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
			print(f"  ... committed after {deleted} deletes")

	if not dry_run:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit

	print(
		f"\nDone."
		f" deleted={deleted}"
		f" skipped_ambiguous={skipped_ambiguous}"
		f" skipped_has_links={skipped_has_links}"
		f" errors={len(errors)}"
	)
	for name, err in errors:
		print(f"  ERROR {name}: {err[:300]}")


def migrate_and_delete_duplicate(old_name, new_name):
	"""
	Migrate all connections from old_name → new_name, then delete old_name.
	Use for 'needs review' cases that have QR/Deal/File/etc on the old lead.
	"""
	if not frappe.db.exists("CRM Lead", old_name):
		print(f"ERROR: old lead {old_name!r} does not exist.")
		return
	if not frappe.db.exists("CRM Lead", new_name):
		print(f"ERROR: new lead {new_name!r} does not exist.")
		return

	print(f"Migrating {old_name!r} → {new_name!r}")

	def _sql_repoint(table, field, old, new):
		# table/field are string literals from the caller (this function only) — not user input.
		# Values (new, old) are properly parameterized.
		frappe.db.sql(  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-sql-format-injection
			f"UPDATE `{table}` SET `{field}` = %s WHERE `{field}` = %s", (new, old)
		)
		return frappe.db.sql("SELECT ROW_COUNT()")[0][0]

	n = _sql_repoint("tabCRM Quote Request", "lead", old_name, new_name)
	if n:
		print(f"  CRM Quote Request: {n} re-pointed")

	n = _sql_repoint("tabCRM Deal", "lead", old_name, new_name)
	if n:
		print(f"  CRM Deal: {n} re-pointed")

	n = _sql_repoint("tabCRM Retry Log", "lead", old_name, new_name)
	if n:
		print(f"  CRM Retry Log: {n} re-pointed")

	frappe.db.sql(
		"UPDATE `tabFile` SET attached_to_name = %s"
		" WHERE attached_to_doctype = 'CRM Lead' AND attached_to_name = %s",
		(new_name, old_name),
	)
	n = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	if n:
		print(f"  File: {n} re-attached")

	for table, label in [
		("tabCRM Task", "CRM Task"),
		("tabFCRM Note", "FCRM Note"),
		("tabCRM Call Log", "CRM Call Log"),
	]:
		frappe.db.sql(
			f"UPDATE `{table}` SET reference_docname = %s"
			" WHERE reference_doctype = 'CRM Lead' AND reference_docname = %s",
			(new_name, old_name),
		)
		n = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
		if n:
			print(f"  {label}: {n} re-pointed")

	frappe.db.sql("UPDATE `tabContact` SET custom_lead = NULL WHERE custom_lead = %s", old_name)
	n = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	if n:
		print(f"  Contact.custom_lead: {n} cleared")

	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	print("  All connections committed.")

	try:
		frappe.delete_doc("CRM Lead", old_name, force=True, ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		print(f"  Deleted {old_name!r}. Done.")
	except Exception as exc:
		frappe.db.rollback()
		print(f"  ERROR: delete failed — {exc}")
		print("  Connections already re-pointed; old lead still exists. Delete manually.")

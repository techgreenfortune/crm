"""
Delete duplicate + test CRM Leads identified from 'CRM Lead (3).xlsx'.

Two modes:
    dry_run=True  — find leads, report connections, no changes
    dry_run=False — delete linked QRs first, clear Contact link, then delete lead

Run:
    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run \
        --kwargs '{"dry_run": true}'

    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run
"""

import frappe

# Creation timestamps from 'dulicates leads' + 'Test leads remove from the crm' sheets.
# Deduplicated (Siddu Test appeared in both sheets with same timestamp).
_TARGET_CREATIONS = [
	"2026-06-06 16:01:20.188000",
	"2026-06-08 16:50:38.204000",
	"2026-06-09 21:43:22.716000",
	"2026-06-10 00:03:36.524000",
	"2026-06-10 00:52:06.942000",
	"2026-06-10 14:13:54.085000",
	"2026-06-10 17:55:00.490000",
	"2026-06-10 17:59:58.498000",
	"2026-06-11 12:03:59.347000",
	"2026-06-11 17:41:25.743000",
	"2026-06-11 17:41:26.384000",
	"2026-06-11 17:41:27.630000",
	"2026-06-11 18:52:04.976000",
	"2026-06-11 19:30:46.739000",
	"2026-06-11 21:44:47.013000",
	"2026-06-12 19:24:36.048000",
	"2026-06-12 19:37:58.989000",
	"2026-06-12 19:38:01.047000",
	"2026-06-16 12:36:39.180000",
	"2026-06-20 15:46:19.104000",
	"2026-06-25 15:19:09.409000",
	"2026-06-26 14:25:32.402000",
	"2026-06-26 14:25:33.513000",
	"2026-06-27 14:41:34.585000",
	"2026-06-27 14:53:11.169000",
	"2026-06-30 15:04:33.107000",
	"2026-06-30 23:52:48.472000",
	"2026-06-30 23:52:49.083000",
	"2026-07-01 17:47:26.099000",
	"2026-07-01 17:47:26.399000",
	"2026-07-01 17:47:26.500000",
	"2026-07-01 17:47:26.598000",
	"2026-07-01 17:47:26.699000",
	"2026-07-01 17:47:26.902000",
	"2026-07-01 17:47:27.003000",
	"2026-07-01 17:47:27.371000",
	"2026-07-01 17:47:27.490000",
	"2026-07-01 17:47:27.594000",
	"2026-07-01 17:47:27.696000",
	"2026-07-01 17:47:27.799000",
	"2026-07-01 17:47:27.897000",
	"2026-07-01 17:47:27.995000",
	"2026-07-01 17:47:28.093000",
	"2026-07-01 17:47:28.195000",
	"2026-07-01 17:47:28.298000",
	"2026-07-01 17:47:28.550000",
	"2026-07-01 17:47:29.053000",
	"2026-07-01 17:47:29.457000",
	"2026-06-04 15:11:05.109000",
	"2026-06-04 15:11:06.080000",
	"2026-06-04 15:11:06.731000",
	"2026-06-04 15:11:07.201000",
	"2026-06-04 15:11:12.329000",
	"2026-06-04 15:11:39.334000",
	"2026-06-04 15:11:55.290000",
	"2026-06-04 15:12:49.078000",
	"2026-06-04 15:13:18.345000",
	"2026-06-04 15:13:24.071000",
	"2026-06-04 15:14:30.630000",
	"2026-06-04 18:06:09.840000",
	"2026-06-04 18:06:10.038000",
	"2026-06-12 01:52:00.479000",
	"2026-06-12 19:37:57.994000",
	"2026-06-20 16:48:14.208000",
	"2026-06-30 23:52:48.374000",
	"2026-07-01 11:52:59.041000",
	"2026-06-04 14:59:05.740000",
	"2026-06-04 15:11:04.656000",
	"2026-06-04 15:12:11.358000",
	"2026-06-04 15:12:54.486000",
	"2026-06-04 18:06:10.893000",
	"2026-06-04 18:06:21.072000",
	"2026-06-04 15:11:07.614000",
	"2026-06-10 13:44:11.968000",
	"2026-06-27 15:47:00.526000",
	"2026-07-01 17:47:28.954000",
	"2026-06-05 12:00:29.310000",
	"2026-06-10 15:59:49.411000",
	"2026-06-11 16:05:44.157000",
	"2026-06-12 19:37:55.924000",
	"2026-06-12 19:37:58.696000",
	# Test leads sheet
	"2026-06-05 12:05:41.961000",
	"2026-06-05 12:55:02.957000",
	"2026-06-11 15:45:27.666000",
	"2026-06-29 15:12:42.309000",
	# "2026-06-30 15:04:33.107000",  # already in dulicates leads
	"2026-07-02 16:04:46.482000",
	"2026-07-06 18:25:55.909000",
	"2026-06-04 14:57:48.598000",
	"2026-06-30 15:08:16.177000",
	"2026-06-11 14:23:45.585000",
	"2026-06-11 15:18:23.733000",
	"2026-06-04 14:57:45.297000",
	"2026-06-09 14:25:58.545000",
	"2026-06-12 01:30:04.211000",
]


def _find_targets():
	placeholders = ", ".join(["%s"] * len(_TARGET_CREATIONS))
	return frappe.db.sql(
		f"SELECT name, first_name, mobile_no, creation FROM `tabCRM Lead`"
		f" WHERE creation IN ({placeholders})"
		f" ORDER BY creation",
		_TARGET_CREATIONS,
		as_dict=True,
	)


def _get_connections(lead_name):
	return {
		"qr": frappe.db.get_all("CRM Quote Request", {"lead": lead_name}, pluck="name"),
		"deal": frappe.db.get_all("CRM Deal", {"lead": lead_name}, pluck="name"),
		"retry_log": frappe.db.get_all("CRM Retry Log", {"lead": lead_name}, pluck="name"),
	}


def run(dry_run=False):
	"""
	Find target leads by creation timestamp, report connections, then delete.
	Linked QRs are deleted first; Deals and RetryLogs are reported but leads
	are still deleted (force=True bypasses link check).
	"""
	targets = _find_targets()
	print(f"Matched {len(targets)} / {len(_TARGET_CREATIONS)} leads in DB")

	not_found = len(_TARGET_CREATIONS) - len(targets)
	if not_found:
		found_ts = {str(r["creation"]) for r in targets}
		missing = [ts for ts in _TARGET_CREATIONS if ts not in found_ts]
		print(f"  NOT FOUND ({not_found}):")
		for ts in missing:
			print(f"    {ts}")

	print()

	deleted = 0
	errors = []

	for lead in targets:
		name = lead["name"]
		conn = _get_connections(name)
		has_conn = conn["qr"] or conn["deal"] or conn["retry_log"]

		if dry_run:
			conn_str = ""
			if conn["qr"]:
				conn_str += f" qr={conn['qr']}"
			if conn["deal"]:
				conn_str += f" deal={conn['deal']}"
			if conn["retry_log"]:
				conn_str += f" retry={conn['retry_log']}"
			flag = " [HAS CONNECTIONS]" if has_conn else ""
			print(f"[DRY RUN] {name}  {lead['first_name']!r}{flag}{conn_str}")
			deleted += 1
			continue

		try:
			# Delete linked QRs first (they link back to this lead)
			for qr_name in conn["qr"]:
				frappe.delete_doc("CRM Quote Request", qr_name, force=True, ignore_permissions=True)
				print(f"  Deleted QR {qr_name!r} (was linked to {name})")

			# Clear Contact.custom_lead
			frappe.db.sql(
				"UPDATE `tabContact` SET custom_lead = NULL WHERE custom_lead = %s",
				name,
			)

			# Delete the lead (force=True bypasses remaining link checks for Deal/RetryLog)
			frappe.delete_doc("CRM Lead", name, force=True, ignore_permissions=True)
			deleted += 1

			if conn["deal"]:
				print(f"  NOTE: Deal(s) {conn['deal']} now have dangling lead ref — {name} deleted")
			if conn["retry_log"]:
				print(f"  NOTE: RetryLog(s) {conn['retry_log']} now have dangling lead ref — {name} deleted")

		except Exception as exc:
			frappe.db.rollback()
			errors.append((name, str(exc)))
			print(f"  ERROR deleting {name}: {exc}")
			continue

		if deleted > 0 and deleted % 20 == 0:
			frappe.db.commit()
			print(f"  ... committed after {deleted} deletes")

	if not dry_run:
		frappe.db.commit()

	print(f"\nDone. deleted={deleted} errors={len(errors)}")
	for name, err in errors:
		print(f"  ERROR {name}: {err[:300]}")

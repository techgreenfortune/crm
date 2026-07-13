"""
Delete duplicate + test CRM Leads identified from 'CRM Lead (3).xlsx'.

Matching uses a timezone-free string comparison:
  CONCAT(DATE_FORMAT(creation, '%Y-%m-%d %H:%i:%s.'), LPAD(ROUND(MICROSECOND(creation)/1000), 3, '0'))
This rounds DB microseconds to the nearest millisecond and compares against ms-precision IST
strings derived from the Excel, avoiding any UNIX_TIMESTAMP timezone dependency.

Run:
    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run \
        --kwargs '{"dry_run": True}'

    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run
"""

import frappe

# ms-precision IST datetime strings from 'dulicates leads' + 'Test leads remove from the crm'.
# Format: 'YYYY-MM-DD HH:MM:SS.mmm' where mmm = round(microseconds/1000).
_TARGET_CREATION_MS = [
	"2026-06-04 14:57:45.297",  # Indiframetestq  mob=8345235664
	"2026-06-04 14:57:48.598",  # Test            mob=9900111222
	"2026-06-04 14:59:05.740",  # Surya           mob=7899083930
	"2026-06-04 15:11:04.656",  # Sekhar          mob=9052228598
	"2026-06-04 15:11:05.109",  # Vamshi          mob=8883999976
	"2026-06-04 15:11:06.080",  # Chandrahas      mob=9505498165
	"2026-06-04 15:11:06.731",  # Nagaraju        mob=9441312449
	"2026-06-04 15:11:07.201",  # Nayan           mob=9908671717
	"2026-06-04 15:11:07.614",  # Shriya          mob=7075743991
	"2026-06-04 15:11:12.329",  # Siva            mob=9849976783
	"2026-06-04 15:11:39.334",  # Venkat          mob=9059019495
	"2026-06-04 15:11:55.290",  # Sreenu          mob=9642354572
	"2026-06-04 15:12:11.358",  # Shankar         mob=9441534746
	"2026-06-04 15:12:49.078",  # KADRU           mob=9440551878
	"2026-06-04 15:12:54.486",  # Sashi           mob=8886813177
	"2026-06-04 15:13:18.345",  # Venkatesh       mob=9391342723
	"2026-06-04 15:13:24.071",  # Balaraju        mob=7396975185
	"2026-06-04 15:14:30.630",  # Karthik         mob=9959602205
	"2026-06-04 18:06:09.840",  # Prakash         mob=9010346631
	"2026-06-04 18:06:10.038",  # Basha           mob=9490888746
	"2026-06-04 18:06:10.893",  # Shaheda         mob=6303342161
	"2026-06-04 18:06:21.072",  # Sharukh         mob=8121235209
	"2026-06-05 12:00:29.310",  # siddu           mob=8686934847
	"2026-06-05 12:05:41.961",  # test-1          mob=6593347093
	"2026-06-05 12:55:02.957",  # Testing order   mob=8383046181
	"2026-06-06 16:01:20.188",  # Hemant          mob=9112012676
	"2026-06-08 16:50:38.204",  # Hemant          mob=9112012676
	"2026-06-09 14:25:58.545",  # Shiva Test      mob=None
	"2026-06-09 21:43:22.716",  # Namita          mob=8806667526
	"2026-06-10 00:03:36.524",  # Manav           mob=9819634377
	"2026-06-10 00:52:06.942",  # Adi             mob=9372673677
	"2026-06-10 13:44:11.968",  # Harika          mob=9640511522
	"2026-06-10 14:13:54.085",  # rahil           mob=9970431118
	"2026-06-10 15:59:49.411",  # Mohan           mob=9822066887
	"2026-06-10 17:55:00.490",  # Rahil Raza      mob=9970431118
	"2026-06-10 17:59:58.498",  # Nitin           mob=7715872233
	"2026-06-11 12:03:59.347",  # Shraddha        mob=9768363672
	"2026-06-11 14:23:45.585",  # Test Lead       mob=9898989898
	"2026-06-11 15:18:23.733",  # Test            mob=9797979797
	"2026-06-11 15:45:27.666",  # Test Lead       mob=7676767676
	"2026-06-11 16:05:44.157",  # Mohan lagdive   mob=9822066887
	"2026-06-11 17:41:25.743",  # LIHAAN          mob=9372673677
	"2026-06-11 17:41:26.384",  # Manav           mob=9819634377
	"2026-06-11 17:41:27.630",  # Namita          mob=8806667526
	"2026-06-11 18:52:04.976",  # Arvind          mob=9004383877
	"2026-06-11 19:30:46.739",  # PRAVIN          mob=9869967106
	"2026-06-11 21:44:47.013",  # Dilip           mob=9820253549
	"2026-06-12 01:30:04.211",  # piyush test     mob=7049216515
	"2026-06-12 01:52:00.479",  # Manish          mob=9765989664
	"2026-06-12 19:24:36.048",  # Mithin          mob=7715872233
	"2026-06-12 19:37:55.924",  # Shraddha Mhadse mob=9768363672
	"2026-06-12 19:37:57.994",  # Dilip Dingankar mob=9820253549
	"2026-06-12 19:37:58.696",  # Pravin          mob=9869967106
	"2026-06-12 19:37:58.989",  # Arvind Bhadekar mob=9004383877
	"2026-06-12 19:38:01.047",  # Manish Tudu     mob=9765989664
	"2026-06-16 12:36:39.180",  # Elara Sculpted Sunlit Villas mob=9908671717
	"2026-06-20 15:46:19.104",  # Ganesh          mob=9603478077
	"2026-06-20 16:48:14.208",  # Acacia homes    mob=9885244427
	"2026-06-25 15:19:09.409",  # shriya with Grill mob=7075743991
	"2026-06-26 14:25:32.402",  # Azim            mob=9820718730
	"2026-06-26 14:25:33.513",  # Azim            mob=9820718730
	"2026-06-27 14:41:34.585",  # Jaleel          mob=9440866853
	"2026-06-27 14:53:11.169",  # suresh kumar    mob=6305767951
	"2026-06-27 15:47:00.526",  # Sashi Kumar     mob=8886813177
	"2026-06-29 15:12:42.309",  # Test            mob=9381560435
	"2026-06-30 15:04:33.107",  # Siddu Test      mob=8686934847
	"2026-06-30 15:08:16.177",  # Test -03        mob=8686934878
	"2026-06-30 23:52:48.374",  # Srinivas        mob=9948141081
	"2026-06-30 23:52:48.472",  # Srinivas        mob=9948141081
	"2026-06-30 23:52:49.083",  # Acacia Homes    mob=9885244427
	"2026-07-01 11:52:59.041",  # Karthik Dasari  mob=9959602205
	"2026-07-01 17:47:26.099",  # Sekhar          mob=9052228598
	"2026-07-01 17:47:26.399",  # Vamsi Krishna   mob=8883999976
	"2026-07-01 17:47:26.500",  # Rishi           mob=9505498165
	"2026-07-01 17:47:26.598",  # Naga            mob=9441312449
	"2026-07-01 17:47:26.699",  # Prakash         mob=9010346631
	"2026-07-01 17:47:26.902",  # Basha           mob=9490888746
	"2026-07-01 17:47:27.003",  # Siva            mob=9849976783
	"2026-07-01 17:47:27.371",  # Syeda           mob=6303342161
	"2026-07-01 17:47:27.490",  # Shankar         mob=9441534746
	"2026-07-01 17:47:27.594",  # Sreenu          mob=9642354572
	"2026-07-01 17:47:27.696",  # Harika          mob=9640511522
	"2026-07-01 17:47:27.799",  # Ganesh          mob=9603478077
	"2026-07-01 17:47:27.897",  # Suresh          mob=6305767951
	"2026-07-01 17:47:27.995",  # Venkata         mob=9059019495
	"2026-07-01 17:47:28.093",  # Krishna         mob=9440551878
	"2026-07-01 17:47:28.195",  # Bala            mob=7396975185
	"2026-07-01 17:47:28.298",  # Venkatesh       mob=9391342723
	"2026-07-01 17:47:28.550",  # Sharuk          mob=8121235209
	"2026-07-01 17:47:28.954",  # Surya           mob=7899083930
	"2026-07-01 17:47:29.053",  # Harika          mob=9640511522
	"2026-07-01 17:47:29.457",  # Jaleel          mob=9440866853
	"2026-07-02 16:04:46.482",  # testing lead    mob=9391901901
	"2026-07-06 18:25:55.909",  # testing lead    mob=8936725284
]


def _find_targets():
	placeholders = ", ".join(["%s"] * len(_TARGET_CREATION_MS))
	return frappe.db.sql(
		"SELECT name, first_name, mobile_no, creation FROM `tabCRM Lead`"
		" WHERE CONCAT("
		"   DATE_FORMAT(creation, '%%Y-%%m-%%d %%H:%%i:%%s.'),"
		"   LPAD(ROUND(MICROSECOND(creation)/1000), 3, '0')"
		f") IN ({placeholders})"
		" ORDER BY creation",
		_TARGET_CREATION_MS,
		as_dict=True,
	)


def _get_blocking_connections(lead_name):
	return {
		"deal": frappe.db.get_all("CRM Deal", {"lead": lead_name}, pluck="name"),
		"retry_log": frappe.db.get_all("CRM Retry Log", {"lead": lead_name}, pluck="name"),
	}


def run(dry_run=False):
	"""
	Find target leads by ms-precision creation timestamp, then delete.
	Leads with Deal or RetryLog connections are SKIPPED — review manually.
	Contact.custom_lead clearing and QR deletion happen inside the on_trash hook.
	"""
	targets = _find_targets()
	print(f"Matched {len(targets)} leads (targeting 94)")
	print()

	deleted = 0
	skipped = []
	errors = []

	for lead in targets:
		name = lead["name"]
		conn = _get_blocking_connections(name)
		has_blocking = conn["deal"] or conn["retry_log"]

		if has_blocking:
			skipped.append((name, lead["first_name"], conn))
			print(
				f"SKIP {name}  {lead['first_name']!r}"
				f"  deal={conn['deal']} retry={conn['retry_log']}"
				" — review manually"
			)
			continue

		if dry_run:
			print(f"[DRY RUN] would delete: {name}  {lead['first_name']!r}  {lead['creation']}")
			deleted += 1
			continue

		try:
			frappe.delete_doc("CRM Lead", name, force=True, ignore_permissions=True)
			deleted += 1
		except Exception as exc:
			frappe.db.rollback()
			errors.append((name, str(exc)))
			print(f"  ERROR deleting {name}: {exc}")
			continue

		if deleted > 0 and deleted % 20 == 0:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
			print(f"  ... committed after {deleted} deletes")

	if not dry_run:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit

	print(f"\nDone. deleted={deleted} skipped={len(skipped)} errors={len(errors)}")
	for name, err in errors:
		print(f"  ERROR {name}: {err[:300]}")

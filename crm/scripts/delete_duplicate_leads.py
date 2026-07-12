"""
Delete duplicate + test CRM Leads identified from 'CRM Lead (3).xlsx'.

Timestamps are millisecond-precision Unix ms (derived from IST datetimes in the
Excel). Matching uses ROUND(UNIX_TIMESTAMP(creation) * 1000) to survive the
openpyxl ±1ms rounding vs DB microseconds.

Run:
    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run \
        --kwargs '{"dry_run": True}'

    bench --site crm.indiframe.com execute \
        crm.scripts.delete_duplicate_leads.run
"""

import frappe

# Millisecond-precision Unix timestamps (IST) from 'dulicates leads' + 'Test leads remove from the crm'.
# Generated: round(ist_aware.timestamp() * 1000) per row.
_TARGET_MS = [
	1780565265297,  # Indiframetestq  mob=8345235664
	1780565268598,  # Test            mob=9900111222
	1780565345740,  # Surya           mob=7899083930
	1780566064656,  # Sekhar          mob=9052228598
	1780566065109,  # Vamshi          mob=8883999976
	1780566066080,  # Chandrahas      mob=9505498165
	1780566066731,  # Nagaraju        mob=9441312449
	1780566067201,  # Nayan           mob=9908671717
	1780566067614,  # Shriya          mob=7075743991
	1780566072329,  # Siva            mob=9849976783
	1780566099334,  # Venkat          mob=9059019495
	1780566115290,  # Sreenu          mob=9642354572
	1780566131358,  # Shankar         mob=9441534746
	1780566169078,  # KADRU           mob=9440551878
	1780566174486,  # Sashi           mob=8886813177
	1780566198345,  # Venkatesh       mob=9391342723
	1780566204071,  # Balaraju        mob=7396975185
	1780566270630,  # Karthik         mob=9959602205
	1780576569840,  # Prakash         mob=9010346631
	1780576570038,  # Basha           mob=9490888746
	1780576570893,  # Shaheda         mob=6303342161
	1780576581072,  # Sharukh         mob=8121235209
	1780641029310,  # siddu           mob=8686934847
	1780641341961,  # test-1          mob=6593347093
	1780644302957,  # Testing order   mob=8383046181
	1780741880188,  # Hemant          mob=9112012676
	1780917638204,  # Hemant          mob=9112012676
	1780995358545,  # Shiva Test      mob=None
	1781021602716,  # Namita          mob=8806667526
	1781030016524,  # Manav           mob=9819634377
	1781032926942,  # Adi             mob=9372673677
	1781079251968,  # Harika          mob=9640511522
	1781081034085,  # rahil           mob=9970431118
	1781087389411,  # Mohan           mob=9822066887
	1781094300490,  # Rahil Raza      mob=9970431118
	1781094598498,  # Nitin           mob=7715872233
	1781159639347,  # Shraddha        mob=9768363672
	1781168025585,  # Test Lead       mob=9898989898
	1781171303733,  # Test            mob=9797979797
	1781172927666,  # Test Lead       mob=7676767676
	1781174144157,  # Mohan lagdive   mob=9822066887
	1781179885743,  # LIHAAN          mob=9372673677
	1781179886384,  # Manav           mob=9819634377
	1781179887630,  # Namita          mob=8806667526
	1781184124976,  # Arvind          mob=9004383877
	1781186446739,  # PRAVIN          mob=9869967106
	1781194487013,  # Dilip           mob=9820253549
	1781208004211,  # piyush test     mob=7049216515
	1781209320479,  # Manish          mob=9765989664
	1781272476048,  # Mithin          mob=7715872233
	1781273275924,  # Shraddha Mhadse mob=9768363672
	1781273277994,  # Dilip Dingankar mob=9820253549
	1781273278696,  # Pravin          mob=9869967106
	1781273278989,  # Arvind Bhadekar mob=9004383877
	1781273281047,  # Manish Tudu     mob=9765989664
	1781593599180,  # Elara Sculpted Sunlit Villas mob=9908671717
	1781950579104,  # Ganesh          mob=9603478077
	1781954294208,  # Acacia homes    mob=9885244427
	1782380949409,  # shriya with Grill mob=7075743991
	1782464132402,  # Azim            mob=9820718730
	1782464133513,  # Azim            mob=9820718730
	1782551494585,  # Jaleel          mob=9440866853
	1782552191169,  # suresh kumar    mob=6305767951
	1782555420526,  # Sashi Kumar     mob=8886813177
	1782726162309,  # Test            mob=9381560435
	1782812073107,  # Siddu Test      mob=8686934847
	1782812296177,  # Test -03        mob=8686934878
	1782843768374,  # Srinivas        mob=9948141081
	1782843768472,  # Srinivas        mob=9948141081
	1782843769083,  # Acacia Homes    mob=9885244427
	1782886979041,  # Karthik Dasari  mob=9959602205
	1782908246099,  # Sekhar          mob=9052228598
	1782908246399,  # Vamsi Krishna   mob=8883999976
	1782908246500,  # Rishi           mob=9505498165
	1782908246598,  # Naga            mob=9441312449
	1782908246699,  # Prakash         mob=9010346631
	1782908246902,  # Basha           mob=9490888746
	1782908247003,  # Siva            mob=9849976783
	1782908247371,  # Syeda           mob=6303342161
	1782908247490,  # Shankar         mob=9441534746
	1782908247594,  # Sreenu          mob=9642354572
	1782908247696,  # Harika          mob=9640511522
	1782908247799,  # Ganesh          mob=9603478077
	1782908247897,  # Suresh          mob=6305767951
	1782908247995,  # Venkata         mob=9059019495
	1782908248093,  # Krishna         mob=9440551878
	1782908248195,  # Bala            mob=7396975185
	1782908248298,  # Venkatesh       mob=9391342723
	1782908248550,  # Sharuk          mob=8121235209
	1782908248954,  # Surya           mob=7899083930
	1782908249053,  # Harika          mob=9640511522
	1782908249457,  # Jaleel          mob=9440866853
	1782988486482,  # testing lead    mob=9391901901
	1783342555909,  # testing lead    mob=8936725284
]


def _find_targets():
	placeholders = ", ".join(["%s"] * len(_TARGET_MS))
	return frappe.db.sql(
		f"SELECT name, first_name, mobile_no, creation FROM `tabCRM Lead`"
		f" WHERE ROUND(UNIX_TIMESTAMP(creation) * 1000) IN ({placeholders})"
		f" ORDER BY creation",
		_TARGET_MS,
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

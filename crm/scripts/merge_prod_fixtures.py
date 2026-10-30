"""
Three-way merge of a prod fixture export into crm/fixtures.

`bench migrate` re-imports every fixture with force=True, so any Desk edit made on prod
to a record we also ship is overwritten on the next deploy. Run this BEFORE deploying,
on an export taken from prod BEFORE migrate (an export taken after migrate has already
lost the edits):

    # on prod, inside the backend container
    bench --site crm.indiframe.com export-fixtures --app crm
    # copy apps/crm/crm/fixtures out, then locally:
    python3 crm/scripts/merge_prod_fixtures.py <export_dir>            # report only
    python3 crm/scripts/merge_prod_fixtures.py <export_dir> --write    # apply

Versions per record (matched by `name`):
    base   = fixture as last shipped to prod (git ref, default origin/custom/production)
    prod   = the export
    ours   = crm/fixtures in the working tree

Merged field by field: a field changed on only one side takes that side's value; a field
changed differently on both sides is a conflict, keeps ours, and is reported. Records
deleted on either side are never dropped automatically — they are reported instead,
because removing a record from a fixture file does not delete it from any site.

Exits 1 when there are conflicts, so a deploy script can stop on them.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path("crm/fixtures")

# Bookkeeping that differs between any two exports without being a real change.
IGNORED_KEYS = frozenset({"modified", "modified_by", "creation", "owner", "idx"})

MISSING = object()


def load_git(ref, path):
	result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
	if result.returncode != 0:
		return None
	return json.loads(result.stdout)


def by_name(records):
	return {r["name"]: r for r in records or []}


def comparable(record):
	return {k: v for k, v in record.items() if k not in IGNORED_KEYS}


def dump_like(data, original_text):
	"""Serialize matching the existing file's indent and trailing newline, to keep diffs minimal."""
	indent, newline = 1, False
	if original_text:
		lines = original_text.splitlines()
		if len(lines) > 1:
			indent = len(lines[1]) - len(lines[1].lstrip(" ")) or 1
		newline = original_text.endswith("\n")
	return json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if newline else "")


def merge_record(name, base, ours, prod, report):
	"""Field-level three-way merge; returns the merged record."""
	merged = dict(ours)
	changed = False
	for key in sorted((set(base) | set(ours) | set(prod)) - IGNORED_KEYS):
		# Exports write empty fields as null where hand-edited files omit the key; treat them as equal.
		b, o, p = (rec.get(key, MISSING) for rec in (base, ours, prod))
		b, o, p = (MISSING if v is None else v for v in (b, o, p))
		if p == b or p == o:
			continue
		if o == b:
			if p is MISSING:
				merged.pop(key, None)
			else:
				merged[key] = p
			changed = True
			report["from_prod"].append(f"{name}.{key}")
		else:
			report["conflicts"].append(
				f"{name}.{key}\n      base: {_short(b)}\n      ours: {_short(o)}\n      prod: {_short(p)}"
			)
	if changed and prod.get("modified"):
		merged["modified"] = max(str(ours.get("modified") or ""), str(prod["modified"]))
	return merged


def _short(value, limit=160):
	text = "<missing>" if value is MISSING else json.dumps(value, ensure_ascii=False)
	return text if len(text) <= limit else text[:limit] + "…"


def merge_file(fname, base_list, ours_list, prod_list):
	base, ours, prod = by_name(base_list), by_name(ours_list), by_name(prod_list)
	report = {"from_prod": [], "added_in_prod": [], "conflicts": [], "notes": []}
	merged = []

	# Import applies records in order, so the last duplicate is what every site ends up with.
	seen = set()
	for rec in ours_list or []:
		if rec["name"] in seen:
			report["notes"].append(f"{rec['name']}: duplicated in ours — kept the last copy")
		seen.add(rec["name"])

	for name, our_rec in ours.items():
		prod_rec, base_rec = prod.get(name), base.get(name)
		if prod_rec is None:
			if base_rec is not None:
				report["notes"].append(f"{name}: missing from prod export (deleted on prod?) — kept ours")
			merged.append(our_rec)
		elif base_rec is None:
			# Added independently on both sides; base is unknown, so any difference is a conflict.
			merged.append(
				merge_record(name, {}, our_rec, prod_rec, report)
				if comparable(our_rec) != comparable(prod_rec)
				else our_rec
			)
		else:
			merged.append(merge_record(name, base_rec, our_rec, prod_rec, report))

	for name, prod_rec in prod.items():
		if name in ours:
			continue
		if name in base:
			if comparable(prod_rec) != comparable(base[name]):
				report["conflicts"].append(
					f"{name}: removed in ours but edited on prod — left out, decide manually"
				)
			continue
		merged.append(prod_rec)
		report["added_in_prod"].append(name)

	return merged, report


def main():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("export_dir", type=Path, help="Directory holding the prod fixture export (*.json)")
	parser.add_argument(
		"--base", default="origin/custom/production", help="Git ref of what prod last deployed"
	)
	parser.add_argument("--write", action="store_true", help="Write merged files into crm/fixtures")
	args = parser.parse_args()

	if not FIXTURES_DIR.is_dir():
		sys.exit("Run from the repo root (crm/fixtures not found).")
	exports = sorted(args.export_dir.glob("*.json"))
	if not exports:
		sys.exit(f"No .json files in {args.export_dir}")

	total_conflicts = 0
	for export in exports:
		fname = export.name
		our_path = FIXTURES_DIR / fname
		prod_list = json.loads(export.read_text())
		base_list = load_git(args.base, our_path.as_posix())

		if not our_path.exists():
			print(f"\n{fname}: new file from prod ({len(prod_list)} records)")
			if args.write:
				our_path.write_text(dump_like(prod_list, None))
			continue

		our_text = our_path.read_text()
		if base_list is None:
			print(f"\n{fname}: not in {args.base}; treating every difference as a conflict")
		merged, report = merge_file(fname, base_list or [], json.loads(our_text), prod_list)

		if not any(report.values()):
			continue
		print(f"\n{fname}")
		for label, key in (
			("taken from prod", "from_prod"),
			("added on prod", "added_in_prod"),
			("CONFLICT (kept ours)", "conflicts"),
			("note", "notes"),
		):
			for item in report[key]:
				print(f"  {label}: {item}")
		total_conflicts += len(report["conflicts"])

		new_text = dump_like(merged, our_text)
		if args.write and new_text != our_text:
			our_path.write_text(new_text)

	print(f"\n{'Wrote' if args.write else 'Dry run —'} merged fixtures; {total_conflicts} conflict(s).")
	sys.exit(1 if total_conflicts else 0)


if __name__ == "__main__":
	main()

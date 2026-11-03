"""
Pull a fresh fixture export from prod and three-way merge it into crm/fixtures.

Run from the repo root, BEFORE deploying (migrate force-imports fixtures, so an export
taken after deploy has already lost prod's Desk edits):

    python3 crm/scripts/pull_prod_fixtures.py <ssh-host>            # report only
    python3 crm/scripts/pull_prod_fixtures.py <ssh-host> --write    # apply

Why not `bench export-fixtures` inside the container:
  - it uses the container's (deployed) hooks.py, so doctypes added to `fixtures` since the
    last deploy are never exported. Here the list comes from the working-tree hooks.py.
  - it overwrites apps/crm/crm/fixtures, the merge base. Here the export goes to a temp dir,
    and the base is read from the container's git HEAD when available, so an earlier
    in-place export can't corrupt it.

Exits 1 on merge conflicts or when any fixture doctype failed to export (its prod edits
would otherwise be overwritten silently). Use --allow-missing for doctypes that don't
exist on prod yet.

Full deploy runbook: docker/FIXTURES.md
"""

import argparse
import io
import shlex
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

from merge_prod_fixtures import FIXTURES_DIR, fixture_filename, fixture_specs, run_merge

EXPORT_JSON = "frappe.core.doctype.data_import.data_import.export_json"


def build_remote_script(container, site, bench_dir):
	tmp = f"/tmp/crm-fixture-pull-{uuid.uuid4().hex}"
	dexec = f"docker exec -w {shlex.quote(bench_dir)} {shlex.quote(container)}"
	lines = [
		"set -eu",
		f'trap "{dexec} rm -rf {tmp}" EXIT',
		f"{dexec} mkdir -p {tmp}/export {tmp}/deployed",
		f"if {dexec} git -C apps/crm rev-parse -q --verify HEAD >/dev/null 2>&1; then",
		f'  echo "base: container apps/crm HEAD $({dexec} git -C apps/crm rev-parse --short HEAD)" >&2',
		f"  {dexec} sh -c 'git -C apps/crm archive HEAD crm/fixtures | tar -x -C {tmp}/deployed --strip-components=2'",
		"else",
		'  echo "WARNING: no git in container; base is apps/crm/crm/fixtures as-is, unverified" >&2',
		f"  {dexec} sh -c 'cp apps/crm/crm/fixtures/*.json {tmp}/deployed/'",
		"fi",
	]
	for spec in fixture_specs():
		# Same arguments `bench export-fixtures` passes; `bench execute` eval()s --kwargs.
		kwargs = repr(
			{
				"doctype": spec.doctype,
				"path": f"{tmp}/export/{fixture_filename(spec.doctype)}",
				"filters": spec.filters,
				"or_filters": spec.or_filters,
				"order_by": "idx asc, creation asc",
			}
		)
		cmd = f"{dexec} bench --site {shlex.quote(site)} execute {EXPORT_JSON} --kwargs {shlex.quote(kwargs)}"
		label = shlex.quote(spec.doctype)
		# Keep going so one failure doesn't hide the rest; missing files are caught locally.
		lines.append(f"echo exporting {label} >&2; {cmd} >&2 || echo failed: {label} >&2")
	lines.append(f"{dexec} tar -C {tmp} -cf - export deployed")
	return "\n".join(lines) + "\n"


def extract(tar_bytes, out_dir):
	with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
		if hasattr(tarfile, "data_filter"):
			tar.extractall(out_dir, filter="data")
		else:
			tar.extractall(out_dir)


def pull_and_merge(args, out_dir):
	script = build_remote_script(args.container, args.site, args.bench_dir)
	result = subprocess.run(["ssh", args.ssh_host, "bash -s"], input=script.encode(), capture_output=True)
	sys.stderr.write(result.stderr.decode(errors="replace"))
	if result.returncode != 0:
		sys.exit(f"Remote export failed (exit {result.returncode}).")
	extract(result.stdout, out_dir)

	exported = {p.name for p in (out_dir / "export").glob("*.json")}
	failed = [
		spec.doctype
		for spec in fixture_specs()
		if fixture_filename(spec.doctype) not in exported and spec.doctype not in args.allow_missing
	]
	conflicts = run_merge(out_dir / "export", base_dir=out_dir / "deployed", write=args.write)
	if failed:
		print(f"\nEXPORT FAILED, prod edits NOT checked for: {', '.join(failed)}")
	return 1 if conflicts or failed else 0


def main():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("ssh_host", help="ssh destination of the prod docker host")
	parser.add_argument("--container", default="crm-prod-frappe-backend-1")
	parser.add_argument("--site", default="crm.indiframe.com")
	parser.add_argument("--bench-dir", default="/home/frappe/frappe-bench")
	parser.add_argument(
		"--allow-missing", nargs="*", default=[], metavar="DOCTYPE", help="Doctypes not on prod yet"
	)
	parser.add_argument("--keep", type=Path, help="Keep the pulled export/ and deployed/ dirs here")
	parser.add_argument("--write", action="store_true", help="Write merged files into crm/fixtures")
	args = parser.parse_args()

	if not FIXTURES_DIR.is_dir():
		sys.exit("Run from the repo root (crm/fixtures not found).")

	if args.keep:
		args.keep.mkdir(parents=True, exist_ok=True)
		sys.exit(pull_and_merge(args, args.keep))
	with tempfile.TemporaryDirectory(prefix="crm-fixture-pull-") as tmp:
		sys.exit(pull_and_merge(args, Path(tmp)))


if __name__ == "__main__":
	main()

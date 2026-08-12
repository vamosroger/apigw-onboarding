#!/usr/bin/env python3
"""Validate an onboarding request CSV before it reaches AWS.

createapigw.py skips bad rows at provisioning time, which means a typo is only
discovered after earlier rows have already created real APIs. This script checks
the whole file up front so a broken CSV fails the workflow before anything is
created.

Expected columns: Name, Domain, Region, Environment, Pod

Usage:
    python scripts/validate_requests.py --config-file requests/pim_apigw_CHG1234567.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

from naming import derive_name, is_derived

REQUIRED_COLUMNS = ["Name", "Domain", "Region", "Environment", "Pod"]

# Region IDs such as us-east-1 or ap-southeast-2, not regional group codes.
REGION_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")

# Leave empty to accept any non-empty Environment. Populate it — e.g.
# ["dev", "staging", "prod"] — to reject anything outside the list.
ALLOWED_ENVIRONMENTS = []


def validate_rows(rows):
    """Return a list of human-readable problems. Empty list means valid."""
    errors = []
    seen_names = {}

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        name = (row.get("Name") or "").strip()
        domain = (row.get("Domain") or "").strip()
        region = (row.get("Region") or "").strip()
        environment = (row.get("Environment") or "").strip()
        pod = (row.get("Pod") or "").strip()

        if not name:
            errors.append(f"row {index}: Name is empty")
        elif name in seen_names:
            errors.append(f"row {index}: duplicate Name '{name}' (also on row {seen_names[name]})")
        else:
            seen_names[name] = index

        if not domain:
            errors.append(f"row {index}: Domain is empty")

        if not region:
            errors.append(f"row {index}: Region is empty")
        elif not REGION_PATTERN.match(region):
            errors.append(f"row {index}: Region '{region}' is not a valid AWS region ID")

        # Environment and Pod are checked for presence only. Set ALLOWED_ENVIRONMENTS
        # to a list such as ["dev", "staging", "prod"] to restrict the values.
        if not environment:
            errors.append(f"row {index}: Environment is empty")
        elif ALLOWED_ENVIRONMENTS and environment not in ALLOWED_ENVIRONMENTS:
            allowed = ", ".join(ALLOWED_ENVIRONMENTS)
            errors.append(
                f"row {index}: Environment '{environment}' is not one of: {allowed}"
            )

        if not pod:
            errors.append(f"row {index}: Pod is empty")
        elif not pod.isdigit():
            errors.append(f"row {index}: Pod '{pod}' must be digits only, e.g. 1")

        # For environments under the naming rule the Name is generated, not
        # chosen. normalize_requests.py writes it; this confirms it held, so a
        # CSV that skipped that step cannot reach AWS with a hand-typed name.
        if is_derived(environment):
            expected = derive_name(environment, domain, pod)
            if expected and name != expected:
                errors.append(
                    f"row {index}: Name '{name}' should be '{expected}' "
                    f"for a {environment} row on {pod}"
                )

    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", required=True, help="Path to the request CSV")
    args = parser.parse_args(argv)

    config_path = Path(args.config_file)
    if not config_path.is_file():
        print(f"ERROR: Configuration file not found: {config_path}", flush=True)
        return 1

    with config_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [field.strip() for field in (reader.fieldnames or [])]
        rows = list(reader)

    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        print(f"ERROR: {config_path} is missing column(s): {', '.join(missing)}", flush=True)
        print(f"       Expected header: {','.join(REQUIRED_COLUMNS)}", flush=True)
        return 1

    if not rows:
        print(f"ERROR: No rows found in {config_path}", flush=True)
        return 1

    errors = validate_rows(rows)
    if errors:
        print(f"ERROR: {config_path} has {len(errors)} problem(s):", flush=True)
        for error in errors:
            print(f"  - {error}", flush=True)
        return 1

    print(f"OK: {config_path} is valid ({len(rows)} row(s)).", flush=True)
    for row in rows:
        print(
            f"  - {row['Name'].strip()} ({row['Domain'].strip()}) "
            f"in {row['Region'].strip()} "
            f"[env={row['Environment'].strip()}, pod={row['Pod'].strip()}]"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

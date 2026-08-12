#!/usr/bin/env python3
"""Validate an onboarding request CSV before it reaches AWS.

createapigw.py skips bad rows at provisioning time, which means a typo is only
discovered after earlier rows have already created real APIs. This script checks
the whole file up front so a broken CSV fails the workflow before anything is
created.

Expected columns: Domain, Region, Environment, Pod

There is no Name column. The API name is generated from Environment, Pod and
Domain by scripts/naming.py, and written into the file by
normalize_requests.py before this runs. Every Environment used must therefore
have a rule in naming.ENVIRONMENT_PREFIXES, or no name can be produced.

Usage:
    python scripts/validate_requests.py --config-file requests/pim_apigw_CHG1234567.csv
"""

import argparse
import csv
import sys
from pathlib import Path

# The naming and region rules live in createapigw.py so they cannot drift
# between the script that applies them and this one, which checks they held.
from createapigw import (
    ALLOWED_REGIONS,
    ENVIRONMENT_PREFIXES,
    derive_name,
    is_allowed,
    is_derived,
    normalize_region,
)

REQUIRED_COLUMNS = ["Domain", "Region", "Environment", "Pod"]


def validate_rows(rows):
    """Return a list of human-readable problems. Empty list means valid."""
    errors = []
    seen_names = {}

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        domain = (row.get("Domain") or "").strip()
        region = (row.get("Region") or "").strip()
        environment = (row.get("Environment") or "").strip()
        pod = (row.get("Pod") or "").strip()

        if not domain:
            errors.append(f"row {index}: Domain is empty")

        # Checked case-insensitively so this script gives the same answer on a
        # raw request CSV as it does after normalize_requests.py has lowercased
        # the column.
        if not region:
            errors.append(f"row {index}: Region is empty")
        elif not is_allowed(region):
            errors.append(
                f"row {index}: Region '{region}' is not an allowed region. "
                f"{len(ALLOWED_REGIONS)} are allowed — see ALLOWED_REGIONS in "
                f"scripts/regions.py."
            )

        if not environment:
            errors.append(f"row {index}: Environment is empty")
        elif not is_derived(environment):
            known = ", ".join(sorted(ENVIRONMENT_PREFIXES))
            errors.append(
                f"row {index}: Environment '{environment}' has no naming rule. "
                f"Known: {known}. Add it to ENVIRONMENT_PREFIXES in scripts/naming.py."
            )

        if not pod:
            errors.append(f"row {index}: Pod is empty")
        elif not pod.isdigit():
            errors.append(f"row {index}: Pod '{pod}' must be digits only, e.g. 1")

        # The name is generated, so a row that cannot produce one is unusable.
        # The individual field errors above already explain why.
        expected = derive_name(environment, domain, pod)
        if expected:
            if expected in seen_names:
                errors.append(
                    f"row {index}: would generate '{expected}', "
                    f"the same name as row {seen_names[expected]}"
                )
            else:
                seen_names[expected] = index

            # After normalize_requests.py has run the column exists and must
            # agree. Running this script on a raw request CSV skips the check.
            actual = row.get("Name")
            if actual is not None and actual.strip() != expected:
                errors.append(
                    f"row {index}: Name '{actual.strip()}' should be '{expected}'"
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
        name = derive_name(row.get("Environment"), row.get("Domain"), row.get("Pod"))
        print(
            f"  - {name} <- {row['Domain'].strip()} "
            f"in {normalize_region(row['Region'])} "
            f"[env={row['Environment'].strip()}, pod={row['Pod'].strip()}]"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

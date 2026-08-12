#!/usr/bin/env python3
"""Strip URL schemes and trailing slashes from the Domain column of a request CSV.

createapigw.py builds its integration URI as https://{domain}/XWeb/... so a
Domain of "https://example.com" produces "https://https://example.com/XWeb/..."
The API is still created, and still deploys, but every $connect points at an
address that does not exist — a failure you only find when a client tries to
connect.

Rewrites the file in place. On a runner that only touches the checkout, never
the committed file, so the CSV in git keeps whatever the requester wrote.

Usage:
    python scripts/normalize_requests.py --config-file requests/example.csv
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

from naming import derive_name

# Any scheme, not just http/https — wss:// is an easy thing to type here.
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_domain(value):
    """Return the bare host for a Domain cell."""
    cleaned = SCHEME.sub("", (value or "").strip())
    return cleaned.rstrip("/")


def normalize_rows(rows):
    """Normalise each row in place.

    Returns a list of (row_number, field, before, after) for everything changed.
    Domain is cleaned first, because the Name rule reads the cleaned domain.
    """
    changes = []
    for index, row in enumerate(rows, start=2):  # row 1 is the header
        before = row.get("Domain") or ""
        after = normalize_domain(before)
        if after != before:
            row["Domain"] = after
            changes.append((index, "Domain", before, after))

        # Environments listed in naming.ENVIRONMENT_PREFIXES get a generated
        # Name. Anything supplied in the column is replaced, so a hand-typed
        # name can never disagree with the convention.
        if "Name" in row:
            generated = derive_name(
                row.get("Environment"), row.get("Domain"), row.get("Pod")
            )
            current = row.get("Name") or ""
            if generated and generated != current:
                row["Name"] = generated
                changes.append((index, "Name", current, generated))

    return changes


def report(changes, config_path):
    """Print changes and, on a runner, add them to the job summary."""
    for number, field, before, after in changes:
        print(f"  - row {number}: {field} '{before}' -> '{after}'", flush=True)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and changes:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n**Normalised {len(changes)} value(s)** in `{config_path}`\n\n")
            for number, field, before, after in changes:
                handle.write(f"- row {number} `{field}`: `{before}` → `{after}`\n")


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
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not fieldnames or "Domain" not in fieldnames:
        # validate_requests.py reports the missing column properly; don't
        # duplicate that error here, just leave the file alone.
        print(f"No Domain column in {config_path}; nothing to normalise.", flush=True)
        return 0

    changes = normalize_rows(rows)

    if not changes:
        print(f"OK: no Domain values needed normalising in {config_path}.", flush=True)
        return 0

    with config_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Normalised {len(changes)} Domain value(s) in {config_path}:", flush=True)
    report(changes, config_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Skip request rows whose API name already exists in AWS.

API Gateway permits duplicate names, so createapigw.py would happily create a
second API called pdpm1api-pim next to the first, indistinguishable except by
ApiId. This script looks first: any row whose name already exists is reported
with the existing API's details and dropped from the work list.

With --write-remaining it writes a CSV of only the rows still to be created, and
the workflow hands that file to createapigw.py. Rows that already exist are
never passed on, so nothing is duplicated.

One get_apis call per distinct region in the CSV, not one per row.

Credentials come from the standard boto3 chain, same as createapigw.py. The
caller needs apigateway:GET in each region being checked.

Existing names are reported and skipped, and the script still exits 0. Pass
--fail-on-existing to make a collision stop the run instead of skipping.

Usage:
    python scripts/check_existing.py --config-file requests/pim_apigw_CHG1234567.csv \\
        --write-remaining "$RUNNER_TEMP/to_create.csv"
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from regions import normalize_region


def log(message):
    print(message, flush=True)


def rows_by_region(rows):
    """Group (name, domain) by region, skipping rows with either field blank.

    Region is lowercased, both because boto3 needs the real region ID and so
    two spellings of the same region are not checked twice.
    """
    grouped = defaultdict(list)
    for row in rows:
        name = (row.get("Name") or "").strip()
        region = normalize_region(row.get("Region"))
        domain = (row.get("Domain") or "").strip()
        if name and region:
            grouped[region].append((name, domain))
    return dict(grouped)


def fetch_existing(region):
    """Return {name: [api, ...]} for every API in a region.

    A list per name because AWS itself allows duplicates — if there are already
    two, that is worth seeing.
    """
    client = boto3.client("apigatewayv2", region_name=region)
    existing = defaultdict(list)
    token = None
    while True:
        kwargs = {"MaxResults": "100"}
        if token:
            kwargs["NextToken"] = token
        response = client.get_apis(**kwargs)
        for api in response.get("Items", []):
            existing[api.get("Name", "")].append(api)
        token = response.get("NextToken")
        if not token:
            break
    return dict(existing)


def find_collisions(requested, existing_by_region):
    """Pure comparison. Returns a list of collision dicts.

    requested:          {region: [(name, domain), ...]}
    existing_by_region: {region: {name: [api, ...]}}
    """
    collisions = []
    for region, entries in sorted(requested.items()):
        existing = existing_by_region.get(region, {})
        for name, domain in entries:
            matches = existing.get(name, [])
            if matches:
                collisions.append({
                    "region": region,
                    "name": name,
                    "domain": domain,
                    "existing": matches,
                })
    return collisions


def describe(api):
    """One-line description of an existing API."""
    return (
        f"ApiId={api.get('ApiId', '?')} "
        f"protocol={api.get('ProtocolType', '?')} "
        f"endpoint={api.get('ApiEndpoint', '?')}"
    )


def partition_rows(rows, collisions):
    """Split rows into (to_create, skipped).

    A row is skipped when its Name already exists in its Region. Matching is on
    the (region, name) pair, so the same name in two regions is two decisions.
    """
    taken = {(hit["region"], hit["name"]) for hit in collisions}
    to_create, skipped = [], []
    for row in rows:
        key = (normalize_region(row.get("Region")), (row.get("Name") or "").strip())
        (skipped if key in taken else to_create).append(row)
    return to_create, skipped


def write_remaining(path, fieldnames, rows):
    """Write the rows still to be created. Header is written even if empty."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def set_output(name, value):
    """Expose a value to the calling workflow step. No-op outside Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def report(collisions, config_path):
    """Print the existing APIs and, on a runner, add them to the job summary."""
    log("=" * 60)
    log(f"ALREADY EXISTS — SKIPPING {len(collisions)} row(s) from {config_path}")
    log("=" * 60)
    for hit in collisions:
        log(f"  {hit['name']}  ({hit['region']})  [{hit['domain']}]")
        for api in hit["existing"]:
            log(f"      existing API : {describe(api)}")
        log("      not creating a new one.")
    log("=" * 60)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n### Skipped — already exist — `{config_path}`\n\n")
            handle.write("| Name | Region | Existing ApiId | Endpoint |\n")
            handle.write("| --- | --- | --- | --- |\n")
            for hit in collisions:
                for api in hit["existing"]:
                    handle.write(
                        f"| `{hit['name']}` | {hit['region']} | "
                        f"`{api.get('ApiId', '?')}` | `{api.get('ApiEndpoint', '?')}` |\n"
                    )
            handle.write(
                "\nThese APIs already exist and were left alone. "
                "Nothing was created for these rows.\n"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", required=True, help="Path to the request CSV")
    parser.add_argument(
        "--write-remaining",
        metavar="PATH",
        help="write a CSV of only the rows still to be created",
    )
    parser.add_argument(
        "--fail-on-existing",
        action="store_true",
        help="exit 1 when a name already exists, instead of skipping the row",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}")
        return 1

    with config_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    requested = rows_by_region(rows)
    if not requested:
        log(f"No usable rows in {config_path}; nothing to check.")
        if args.write_remaining:
            write_remaining(args.write_remaining, fieldnames, [])
        set_output("remaining", 0)
        set_output("skipped", 0)
        return 0

    existing_by_region = {}
    for region in sorted(requested):
        log(f"Checking existing APIs in {region}...")
        try:
            existing_by_region[region] = fetch_existing(region)
        except (ClientError, BotoCoreError) as error:
            # A check that cannot run must not silently look like a pass.
            log(f"WARNING: could not list APIs in {region}: {error}")
            log("         Names in this region were NOT checked for duplicates.")
            if args.fail_on_existing:
                return 1
            existing_by_region[region] = {}

    collisions = find_collisions(requested, existing_by_region)
    to_create, skipped = partition_rows(rows, collisions)

    if collisions:
        report(collisions, config_path)
    else:
        log(f"OK: none of the {len(rows)} requested API name(s) already exist.")

    log(f"{len(to_create)} row(s) to create, {len(skipped)} already exist.")

    if args.write_remaining:
        write_remaining(args.write_remaining, fieldnames, to_create)
        log(f"Wrote {len(to_create)} row(s) to {args.write_remaining}")

    set_output("remaining", len(to_create))
    set_output("skipped", len(skipped))

    if collisions and args.fail_on_existing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

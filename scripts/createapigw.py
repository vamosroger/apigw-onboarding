#!/usr/bin/env python3
"""Create WebSocket API Gateway endpoints from a CSV configuration file.

Author:       Arnold Ramos
Date Created: 2026

Python port of CreateApiGateway.ps1 (PIM WebSocket provisioning).

Four subcommands, run in this order by .github/workflows/onboard.yml:

    normalize   clean Domain and Region, add the generated Name column
    check       report names that already exist in AWS and drop those rows
    create      create the WebSocket APIs
    report      print the final state of every row

Validation lives in validate_requests.py, which runs between normalize and
check and imports the naming and region rules from here.

For each row in the input CSV, `create` builds a WEBSOCKET API with three routes
($connect, $disconnect, ping), their integrations and responses, a "production"
stage, and a deployment. The resulting WebSocket URLs are printed as a table.
Nothing is written to disk.

Credentials are resolved by the standard boto3 credential chain (environment
variables, AWS_PROFILE, ~/.aws/credentials, SSO cache, container credentials,
or an EC2 instance role). Nothing is passed explicitly. `check` and `report`
additionally need apigateway:GET.

Request CSV format — no Name column, `normalize` adds it:
    Domain,Region,Environment,Pod
    example.com,us-east-1,prod,1

Usage:
    python scripts/createapigw.py normalize --config-file requests/pim_apigw_CHG1234567.csv
    python scripts/createapigw.py check     --config-file <csv> --write-remaining <path>
    python scripts/createapigw.py create    --config-file <path>
    python scripts/createapigw.py report    --config-file <csv> --skipped "name1,name2"
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# Only `check`, `create` and `report` talk to AWS. `normalize` — and
# validate_requests.py, which imports the naming and region rules from here —
# must keep working without boto3 installed, so someone can check a CSV locally
# before opening a PR. get_client() raises a clear error if it is really needed.
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError:  # pragma: no cover - exercised only without boto3
    boto3 = None

    class BotoCoreError(Exception):
        """Placeholder so `except` clauses still resolve without botocore."""

    class ClientError(BotoCoreError):
        """Placeholder so `except` clauses still resolve without botocore."""

# ============================================================
# CONFIGURATION — API CREATION
# ============================================================

ROUTE_KEYS = ["$connect", "$disconnect", "ping"]
API_STAGE = "production"
ROUTE_SELECTION_EXPRESSION = "$request.body.type"

# NOT escaped, unlike TEMPLATE_SELECTION_EXPRESSION below. CreateRoute rejects
# "\$default" with:
#     Currently, only $default is supported as a route response selection
#     expression.
# API Gateway accepts only the plain reserved word here — it is a fixed marker
# meaning "this route handles two-way communication", not a parsed expression.
ROUTE_RESPONSE_SELECTION_EXPRESSION = "$default"

# The backslash is required by API Gateway, not a leftover from PowerShell. In a
# selection expression '$' introduces a variable, so an unescaped "$default" is
# read as a variable named 'default' and AWS rejects it with:
#     Unexpected variable in selection expression: $default
# Escaping it as "\$default" means the literal reserved key. This was briefly
# "corrected" to "$default" during the port, which broke CreateIntegration.
# https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-websocket-api-selection-expressions.html
TEMPLATE_SELECTION_EXPRESSION = "\\$default"

INTEGRATION_RESPONSE_TEMPLATE_SELECTION = "${integration.response.statuscode}"

# Request templates (VTL) keyed by route. The inner "$default" key is the
# template selection expression AWS matches against.
REQUEST_TEMPLATES = {
    "$connect": {
        "$default": (
            '{"connectionID" : "$context.connectionId", '
            '"authCode" : "$input.params().querystring.auth" }'
        )
    },
    "$disconnect": {"$default": '{"connectionID" : "$context.connectionId"}'},
    # NOTE: the PowerShell script had '{ statusCode : 200}' - invalid JSON, the
    # key was unquoted, and a MOCK integration will not respond without valid
    # JSON here. Corrected to {"statusCode": 200}.
    "ping": {"$default": '{"statusCode": 200}'},
}

# Per-route integration settings. URIs are formatted with the row's domain.
INTEGRATION_CONFIG = {
    "$connect": {
        "IntegrationType": "HTTP",
        "IntegrationMethod": "POST",
        "UriTemplate": "https://{domain}/XWeb/Core/WebsocketModule.ashx?function=CompleteConnection",
    },
    "$disconnect": {
        "IntegrationType": "HTTP",
        "IntegrationMethod": "POST",
        "UriTemplate": "https://{domain}/XWeb/Core/WebsocketModule.ashx?function=CloseConnection",
    },
    "ping": {
        "IntegrationType": "MOCK",
        "IntegrationMethod": None,
        "UriTemplate": None,
    },
}

# Response templates applied only to specific routes
INTEGRATION_RESPONSE_TEMPLATES = {
    "ping": {"200": '{"type": "pong"}'},
}

RESULT_HEADER = ["API Name", "Domain", "Websocket URL"]
RESULT_COLUMN_WIDTHS = [28, 32, 0]  # 0 = last column, not padded

# ============================================================
# CONFIGURATION — NAMING RULE
# ============================================================

# APIs are named <prefix><pod>api-<first label of the domain>, e.g. pod 1 plus
# pim.acme.com under prod gives pdpm1api-pim. An Environment not listed here has
# no rule, and validate_requests.py rejects the row rather than guessing.
ENVIRONMENT_PREFIXES = {
    "prod": "pdpm",
    "production": "pdpm",
    "dev": "dvpm",
    "development": "dvpm",
}

# The Pod column holds the number and nothing else — "1", not "pod1".
POD_NUMBER = re.compile(r"^\d+$")

# ============================================================
# CONFIGURATION — ALLOWED REGIONS
# ============================================================

# Region IDs only — us-east-1, not a group code like use1. To allow another
# region, add it here.
ALLOWED_REGIONS = frozenset({
    "af-south-1",
    "ap-east-1",
    "ap-east-2",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-south-1",
    "ap-south-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ap-southeast-4",
    "ap-southeast-5",
    "ap-southeast-6",
    "ap-southeast-7",
    "ca-central-1",
    "ca-west-1",
    "eu-central-1",
    "eu-central-2",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "il-central-1",
    "me-central-1",
    "me-south-1",
    "mx-central-1",
    "sa-east-1",
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
})

# Any scheme, not just http/https — wss:// is an easy thing to type here.
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# Statuses used by the final report.
STATUS_EXISTING = "existing"
STATUS_CREATED = "created"
STATUS_MISSING = "MISSING"

REPORT_HEADER = ["STATUS", "API NAME", "REGION", "API ID", "WEBSOCKET URL"]
REPORT_COLUMN_WIDTHS = [9, 28, 16, 12, 0]

# ============================================================
# CONFIGURATION — CLOUDWATCH LOGGING ROLE
# ============================================================

# API Gateway writes execution logs using an account-level role, set once per
# account per region. Without it, the stage's LoggingLevel is recorded but no
# logs are ever delivered.
APIGW_SERVICE_PRINCIPAL = "apigateway.amazonaws.com"
APIGW_CLOUDWATCH_POLICY = (
    "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
)


# ============================================================
# SHARED HELPERS
# ============================================================

# Set by `create --quiet`. Progress chatter is suppressed; errors are not.
_QUIET = False


def log(message, force=False):
    """Write a message to stdout, flushed so output interleaves correctly.

    Suppressed under --quiet unless force is set. Anything the reader needs in
    order to act — errors, skips, the closing tally — passes force=True.
    """
    if _QUIET and not force:
        return
    print(message, flush=True)


def get_client(region, _cache={}):
    """Return a cached apigatewayv2 client for the given region."""
    if boto3 is None:
        raise RuntimeError(
            "boto3 is not installed, and this command talks to AWS. "
            "Run: pip install -r scripts/requirements.txt"
        )
    if region not in _cache:
        _cache[region] = boto3.client("apigatewayv2", region_name=region)
    return _cache[region]


def set_output(name, value):
    """Expose a value to the calling workflow step. No-op outside Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def format_row(values, widths):
    """Pad a row into aligned columns. A width of 0 means do not pad."""
    cells = []
    for value, width in zip(values, widths):
        cells.append(str(value).ljust(width) if width else str(value))
    return "  ".join(cells).rstrip()


def read_rows(config_path):
    """Read a request CSV. Returns (fieldnames, rows)."""
    with config_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


# ============================================================
# NAMING RULE
# ============================================================

def environment_prefix(environment):
    """Return the name prefix for an Environment, or '' if it has no rule."""
    return ENVIRONMENT_PREFIXES.get((environment or "").strip().lower(), "")


def is_derived(environment):
    """True when this Environment's Name is generated rather than supplied."""
    return bool(environment_prefix(environment))


def pod_number(pod):
    """Return the Pod number, or '' if the cell is not digits only.

    Digits are taken verbatim, so '01' yields '01', not '1'. Anything else —
    'pod1', '1a', '' — returns '' and validate_requests.py reports it.
    """
    value = (pod or "").strip()
    return value if POD_NUMBER.match(value) else ""


def domain_label(domain):
    """First label of a domain: api.eu.example.com -> api.

    Expects a bare host. Run the Domain column through normalize_domain first,
    or a value like 'https://example.com' yields 'https://example'.
    """
    return (domain or "").strip().split(".")[0].strip()


def derive_name(environment, domain, pod):
    """Return the generated API name, or '' if it cannot be derived."""
    prefix = environment_prefix(environment)
    label = domain_label(domain)
    number = pod_number(pod)
    if not prefix or not label or not number:
        return ""
    return f"{prefix}{number}api-{label}".lower()


# ============================================================
# REGIONS
# ============================================================

def normalize_region(value):
    """Lowercase and trim a Region cell. AWS region IDs are always lowercase."""
    return (value or "").strip().lower()


def is_allowed(value):
    """True when the value names an allowed region, whatever its casing."""
    return normalize_region(value) in ALLOWED_REGIONS


# ============================================================
# NORMALIZE
# ============================================================

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

        # AWS region IDs are always lowercase, so US-EAST-1 is a casing slip
        # rather than a different region.
        if "Region" in row:
            before_region = row.get("Region") or ""
            after_region = normalize_region(before_region)
            if after_region != before_region:
                row["Region"] = after_region
                changes.append((index, "Region", before_region, after_region))

        # The request CSV has no Name column — the name is generated here from
        # Environment, Pod and the cleaned Domain, then written into the file so
        # `create` can read it. A row that cannot produce a name gets an empty
        # one, and validate_requests.py explains why.
        generated = derive_name(row.get("Environment"), row.get("Domain"), row.get("Pod"))
        current = row.get("Name") or ""
        if generated != current:
            row["Name"] = generated
            changes.append((index, "Name", current, generated))
        else:
            row["Name"] = current

    return changes


def report_normalised(changes, config_path):
    """Print changes and, on a runner, note them in the job summary."""
    for number, field, before, after in changes:
        log(f"  - row {number}: {field} '{before}' -> '{after}'")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and changes:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n**Normalised {len(changes)} value(s)** in `{config_path}`\n\n")
            for number, field, before, after in changes:
                handle.write(f"- row {number} `{field}`: `{before}` → `{after}`\n")


def cmd_normalize(args):
    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}")
        return 1

    fieldnames, rows = read_rows(config_path)

    if not fieldnames or "Domain" not in fieldnames:
        # validate_requests.py reports the missing column properly; don't
        # duplicate that error here, just leave the file alone.
        log(f"No Domain column in {config_path}; nothing to normalise.")
        return 0

    changes = normalize_rows(rows)

    # Prepend rather than append so the generated name is the first thing you
    # see in the file.
    if "Name" not in fieldnames:
        fieldnames = ["Name"] + fieldnames

    if not changes:
        log(f"OK: nothing needed normalising in {config_path}.")
        return 0

    with config_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log(f"Normalised {len(changes)} value(s) in {config_path}:")
    report_normalised(changes, config_path)
    return 0


# ============================================================
# CHECK FOR EXISTING APIS
# ============================================================

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


def fetch_apis(region):
    """Return {name: [api, ...]} for every API in a region.

    A list per name because AWS itself allows duplicates — if there are already
    two, that is worth seeing.
    """
    client = get_client(region)
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


def report_collisions(collisions, config_path):
    """Print the existing APIs. The job summary is written by `report`."""
    log("=" * 60)
    log(f"ALREADY EXISTS — SKIPPING {len(collisions)} row(s) from {config_path}")
    log("=" * 60)
    for hit in collisions:
        log(f"  {hit['name']}  ({hit['region']})  [{hit['domain']}]")
        for api in hit["existing"]:
            log(f"      existing API : {describe(api)}")
        log("      not creating a new one.")
    log("=" * 60)


def cmd_check(args):
    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}")
        return 1

    fieldnames, rows = read_rows(config_path)

    requested = rows_by_region(rows)
    if not requested:
        log(f"No usable rows in {config_path}; nothing to check.")
        if args.write_remaining:
            write_remaining(args.write_remaining, fieldnames, [])
        set_output("remaining", 0)
        set_output("skipped", 0)
        set_output("skipped_names", "")
        return 0

    existing_by_region = {}
    for region in sorted(requested):
        log(f"Checking existing APIs in {region}...")
        try:
            existing_by_region[region] = fetch_apis(region)
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
        report_collisions(collisions, config_path)
    else:
        log(f"OK: none of the {len(rows)} requested API name(s) already exist.")

    log(f"{len(to_create)} row(s) to create, {len(skipped)} already exist.")

    if args.write_remaining:
        write_remaining(args.write_remaining, fieldnames, to_create)
        log(f"Wrote {len(to_create)} row(s) to {args.write_remaining}")

    set_output("remaining", len(to_create))
    set_output("skipped", len(skipped))
    # Names, not just the count, so `report` can label each row as pre-existing
    # rather than newly created.
    set_output("skipped_names", ",".join((row.get("Name") or "").strip() for row in skipped))

    if collisions and args.fail_on_existing:
        return 1
    return 0


# ============================================================
# CLOUDWATCH LOGGING ROLE
# ============================================================

def get_apigateway_client(region, _cache={}):
    """apigatewayv2 has no account settings — those live on the v1 client."""
    if boto3 is None:
        raise RuntimeError(
            "boto3 is not installed, and this command talks to AWS. "
            "Run: pip install -r scripts/requirements.txt"
        )
    if region not in _cache:
        _cache[region] = boto3.client("apigateway", region_name=region)
    return _cache[region]


def get_iam_client(_cache={}):
    """IAM is global; the region only decides which endpoint is called."""
    if boto3 is None:
        raise RuntimeError(
            "boto3 is not installed, and this command talks to AWS. "
            "Run: pip install -r scripts/requirements.txt"
        )
    if "client" not in _cache:
        _cache["client"] = boto3.client("iam")
    return _cache["client"]


def trusts_apigateway(assume_role_policy):
    """True when a trust policy lets API Gateway assume the role.

    boto3 usually hands back a decoded dict, but accept a JSON string too.
    """
    if isinstance(assume_role_policy, str):
        try:
            assume_role_policy = json.loads(assume_role_policy)
        except (TypeError, ValueError):
            return False
    if not isinstance(assume_role_policy, dict):
        return False

    # A single-statement policy may be a bare object rather than a list, and a
    # malformed one may be neither. Neither should raise.
    statements = assume_role_policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list):
        return False

    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal")
        if not isinstance(principal, dict):
            continue
        services = principal.get("Service", [])
        if isinstance(services, str):
            services = [services]
        if not isinstance(services, list):
            continue
        if APIGW_SERVICE_PRINCIPAL in services:
            return True
    return False


def choose_role(candidates):
    """Pick the role to use from [(name, arn, has_policy), ...].

    A role carrying the AWS managed CloudWatch policy is preferred. Ambiguity
    is refused rather than guessed at — this grants a role to the whole
    account, so picking the wrong one is worse than doing nothing.
    Returns (arn, reason) with arn None when no safe choice exists.
    """
    if not candidates:
        return None, "no IAM role trusts apigateway.amazonaws.com"

    with_policy = [c for c in candidates if c[2]]
    if len(with_policy) == 1:
        return with_policy[0][1], f"{with_policy[0][0]} has {APIGW_CLOUDWATCH_POLICY}"
    if len(with_policy) > 1:
        names = ", ".join(c[0] for c in with_policy)
        return None, f"several roles carry the CloudWatch policy ({names}) — pass --role-arn"

    if len(candidates) == 1:
        return candidates[0][1], f"{candidates[0][0]} is the only role trusting API Gateway"

    names = ", ".join(c[0] for c in candidates)
    return None, f"several roles trust API Gateway ({names}) — pass --role-arn"


def find_apigateway_roles(iam):
    """Return [(name, arn, has_cloudwatch_policy), ...] for roles API Gateway can assume."""
    candidates = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page.get("Roles", []):
            if not trusts_apigateway(role.get("AssumeRolePolicyDocument")):
                continue
            attached = iam.list_attached_role_policies(RoleName=role["RoleName"])
            has_policy = any(
                policy.get("PolicyArn") == APIGW_CLOUDWATCH_POLICY
                for policy in attached.get("AttachedPolicies", [])
            )
            candidates.append((role["RoleName"], role["Arn"], has_policy))
    return candidates


def cmd_cloudwatch(args):
    """Ensure each region has an account-level CloudWatch logging role."""
    regions = []
    if args.region:
        regions = [normalize_region(args.region)]
    else:
        config_path = Path(args.config_file)
        if not config_path.is_file():
            log(f"ERROR: Configuration file not found: {config_path}")
            return 1
        _, rows = read_rows(config_path)
        regions = sorted({normalize_region(row.get("Region")) for row in rows} - {""})

    if not regions:
        log("No regions to check.")
        return 0

    role_arn = args.role_arn
    role_reason = "supplied with --role-arn" if role_arn else ""
    problems = 0

    for region in regions:
        log(f"===== CloudWatch logging role — {region} =====")
        try:
            account = get_apigateway_client(region).get_account()
        except (ClientError, BotoCoreError) as error:
            log(f"  ERROR: could not read API Gateway account settings: {error}")
            problems += 1
            continue

        configured = (account.get("cloudwatchRoleArn") or "").strip()
        if configured:
            log(f"  Already configured: {configured}")
            continue

        log("  Not configured. Looking for a suitable IAM role...")

        # IAM is global, so discover once and reuse across regions.
        if not role_arn:
            try:
                candidates = find_apigateway_roles(get_iam_client())
            except (ClientError, BotoCoreError) as error:
                log(f"  ERROR: could not query IAM: {error}")
                problems += 1
                continue

            for name, arn, has_policy in candidates:
                marker = " (has the CloudWatch policy)" if has_policy else ""
                log(f"    candidate: {name}{marker}")

            role_arn, role_reason = choose_role(candidates)

        if not role_arn:
            log(f"  SKIPPED: {role_reason}")
            log("           Execution logs will not be delivered until a role is set.")
            problems += 1
            continue

        log(f"  Using {role_arn} — {role_reason}")
        try:
            get_apigateway_client(region).update_account(
                patchOperations=[
                    {"op": "replace", "path": "/cloudwatchRoleArn", "value": role_arn}
                ]
            )
        except (ClientError, BotoCoreError) as error:
            log(f"  ERROR: could not set the CloudWatch role: {error}")
            problems += 1
            continue

        log(f"  Configured {region} to log with {role_arn}")
        log("  NOTE: this is an account-wide setting — it affects every API in "
            f"{region}, not just the ones created by this run.")

    if problems and args.require:
        return 1
    # Logging config must not block provisioning unless asked to.
    return 0


# ============================================================
# API CREATION
# ============================================================

def create_integrations(client, api_id, api_domain):
    """Create one integration per route. Returns {route_key: integration_id}."""
    integration_ids = {}
    for route_key in ROUTE_KEYS:
        config = INTEGRATION_CONFIG[route_key]
        params = {
            "ApiId": api_id,
            "IntegrationType": config["IntegrationType"],
            "TemplateSelectionExpression": TEMPLATE_SELECTION_EXPRESSION,
            "RequestTemplates": REQUEST_TEMPLATES[route_key],
        }
        if config["IntegrationMethod"]:
            params["IntegrationMethod"] = config["IntegrationMethod"]
        if config["UriTemplate"]:
            params["IntegrationUri"] = config["UriTemplate"].format(domain=api_domain)

        response = client.create_integration(**params)
        integration_ids[route_key] = response["IntegrationId"]
        log(f"    Integration created for {route_key}: {response['IntegrationId']}")
    return integration_ids


def create_integration_responses(client, api_id, integration_ids):
    """Create the $default integration response for each integration."""
    for route_key in ROUTE_KEYS:
        params = {
            "ApiId": api_id,
            "IntegrationId": integration_ids[route_key],
            "IntegrationResponseKey": "$default",
            "TemplateSelectionExpression": INTEGRATION_RESPONSE_TEMPLATE_SELECTION,
        }
        if route_key in INTEGRATION_RESPONSE_TEMPLATES:
            params["ResponseTemplates"] = INTEGRATION_RESPONSE_TEMPLATES[route_key]

        client.create_integration_response(**params)
        log(f"    Integration response created for {route_key}")


def create_routes(client, api_id, integration_ids):
    """Create one route per route key. Returns {route_key: route_id}."""
    route_ids = {}
    for route_key in ROUTE_KEYS:
        response = client.create_route(
            ApiId=api_id,
            RouteKey=route_key,
            RouteResponseSelectionExpression=ROUTE_RESPONSE_SELECTION_EXPRESSION,
            Target=f"integrations/{integration_ids[route_key]}",
        )
        route_ids[route_key] = response["RouteId"]
        log(f"    Route created for {route_key}: {response['RouteId']}")
    return route_ids


def create_route_responses(client, api_id, route_ids):
    """Create the $default route response for each route."""
    for route_key in ROUTE_KEYS:
        client.create_route_response(
            ApiId=api_id,
            RouteId=route_ids[route_key],
            RouteResponseKey="$default",
        )
        log(f"    Route response created for {route_key}")


def provision_api(api_name, api_domain, api_region):
    """Create and deploy a single WebSocket API. Returns the WebSocket URL."""
    client = get_client(api_region)

    log(f"  Creating WebSocket API in {api_region}...")
    api = client.create_api(
        Name=api_name,
        ProtocolType="WEBSOCKET",
        RouteSelectionExpression=ROUTE_SELECTION_EXPRESSION,
    )
    api_id = api["ApiId"]
    log(f"  API created: {api_id}")

    integration_ids = create_integrations(client, api_id, api_domain)
    create_integration_responses(client, api_id, integration_ids)
    route_ids = create_routes(client, api_id, integration_ids)
    create_route_responses(client, api_id, route_ids)

    client.create_stage(
        ApiId=api_id,
        StageName=API_STAGE,
        DefaultRouteSettings={"LoggingLevel": "ERROR"},
    )
    log(f"  Stage '{API_STAGE}' created")

    # Only deploy once every route was created successfully
    if len(route_ids) != len(ROUTE_KEYS):
        raise RuntimeError(
            f"Expected {len(ROUTE_KEYS)} routes but created {len(route_ids)}; skipping deployment"
        )

    client.create_deployment(ApiId=api_id, StageName=API_STAGE)
    log(f"  Deployed to stage '{API_STAGE}'")

    # create_api already returns ApiEndpoint, so no follow-up get_api is needed
    return api["ApiEndpoint"]


def print_results(created):
    """Print the created APIs as a table. Nothing is written to disk.

    Suppressed by --quiet — the `report` subcommand prints the same information
    from live AWS state at the end of the job.
    """
    if not created:
        log("\nNo APIs were created.")
        return

    rule = "=" * 90
    log("")
    log(rule)
    log(format_row(RESULT_HEADER, RESULT_COLUMN_WIDTHS))
    log(rule)
    for row in created:
        log(format_row(row, RESULT_COLUMN_WIDTHS))
    log(rule)


def cmd_create(args):
    global _QUIET
    _QUIET = args.quiet

    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}", force=True)
        return 1

    _, rows = read_rows(config_path)

    if not rows:
        log(f"ERROR: No rows found in {config_path}", force=True)
        return 1

    # API Gateway permits duplicate names, so without this a re-run creates a
    # second, indistinguishable API for every row. One get_apis call per
    # region, before anything is created.
    requested = rows_by_region(rows)
    existing_by_region = {}
    for region in sorted(requested):
        try:
            existing_by_region[region] = fetch_apis(region)
        except (ClientError, BotoCoreError) as error:
            # A check that cannot run must not silently look like a pass.
            log(f"WARNING: could not list APIs in {region}: {error}", force=True)
            log(f"         Rows in {region} were NOT checked and may duplicate.", force=True)
            existing_by_region[region] = {}

    collisions = find_collisions(requested, existing_by_region)
    rows, skipped = partition_rows(rows, collisions)

    if collisions:
        for hit in collisions:
            log(
                f"SKIPPED: '{hit['name']}' already exists in {hit['region']} "
                f"-> {describe(hit['existing'][0])}",
                force=True,
            )

    # Names, not just the count, so `report` can label these rows as
    # pre-existing rather than newly created.
    set_output("skipped_names", ",".join((row.get("Name") or "").strip() for row in skipped))

    if not rows:
        log("Nothing to create — every requested API already exists.", force=True)
        return 0

    created = []
    failed = 0

    for row in rows:
        api_name = (row.get("Name") or "").strip()
        api_domain = (row.get("Domain") or "").strip()
        api_region = normalize_region(row.get("Region"))

        if not api_name:
            log(f"ERROR: Row missing Name in {config_path}. Skipping.", force=True)
            failed += 1
            continue

        # Skip the row if no Region was supplied, rather than defaulting to null
        if not api_region:
            log(
                f"ERROR: No Region specified for '{api_name}' in {config_path}. Skipping.",
                force=True,
            )
            failed += 1
            continue

        log(f"===== Processing '{api_name}' ({api_domain}) in {api_region} =====")
        try:
            api_url = provision_api(api_name, api_domain, api_region)
            created.append([api_name, api_domain, api_url])
            log(f"SUCCESS: '{api_name}' -> {api_url}")
        except (ClientError, BotoCoreError, RuntimeError) as error:
            # A failure here may leave a partially built API in AWS - the
            # `report` subcommand lists what actually exists afterwards.
            log(f"ERROR: Failed to create '{api_name}' in {api_region}: {error}", force=True)
            failed += 1

    print_results(created)
    log(
        f"\nDone. {len(created)} created, {len(skipped)} already existed, {failed} failed.",
        force=True,
    )

    return 1 if failed else 0


# ============================================================
# FINAL REPORT
# ============================================================

def first_by_name(grouped):
    """Collapse {name: [api, ...]} to {name: api}, keeping the first."""
    return {name: apis[0] for name, apis in grouped.items() if apis}


def build_report(rows, live_by_region, skipped_names):
    """Pure. Returns a list of (status, name, region, api_id, url)."""
    report = []
    for row in rows:
        name = (row.get("Name") or "").strip()
        region = normalize_region(row.get("Region"))
        if not name:
            continue

        api = live_by_region.get(region, {}).get(name)
        if api is None:
            report.append((STATUS_MISSING, name, region, "-", "-"))
            continue

        status = STATUS_EXISTING if name in skipped_names else STATUS_CREATED
        report.append((
            status,
            name,
            region,
            api.get("ApiId", "?"),
            api.get("ApiEndpoint", "?"),
        ))
    return report


def counts(report):
    """Return (existing, created, missing)."""
    tally = defaultdict(int)
    for status, *_ in report:
        tally[status] += 1
    return tally[STATUS_EXISTING], tally[STATUS_CREATED], tally[STATUS_MISSING]


def print_report(report, config_path):
    rule = "=" * 110
    existing, created, missing = counts(report)

    log(rule)
    log(f"FINAL STATE — {config_path}")
    log(rule)
    log(format_row(REPORT_HEADER, REPORT_COLUMN_WIDTHS))
    log("-" * 110)
    for entry in report:
        log(format_row(entry, REPORT_COLUMN_WIDTHS))
    log(rule)
    log(f"{existing} already existed, {created} created, {missing} missing.")
    if missing:
        log("")
        log("MISSING rows produced no API. Check the errors above before re-running.")


def write_step_summary(report, config_path):
    """Write the run's job summary. This is the only place that writes it."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path or not report:
        return
    existing, created, missing = counts(report)
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(f"\n### Final state — `{config_path}`\n\n")
        handle.write("| Status | API Name | Region | API ID | WebSocket URL |\n")
        handle.write("| --- | --- | --- | --- | --- |\n")
        for status, name, region, api_id, url in report:
            mark = {STATUS_CREATED: "🆕", STATUS_EXISTING: "♻️"}.get(status, "⚠️")
            handle.write(f"| {mark} {status} | `{name}` | {region} | `{api_id}` | `{url}` |\n")
        handle.write(f"\n{existing} already existed, {created} created, {missing} missing.\n")


def cmd_report(args):
    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}")
        return 1

    _, rows = read_rows(config_path)

    if not rows:
        log(f"No rows in {config_path}; nothing to report.")
        return 0

    skipped_names = {name.strip() for name in args.skipped.split(",") if name.strip()}

    live_by_region = {}
    for region in sorted({normalize_region(row.get("Region")) for row in rows} - {""}):
        try:
            live_by_region[region] = first_by_name(fetch_apis(region))
        except (ClientError, BotoCoreError) as error:
            log(f"WARNING: could not list APIs in {region}: {error}")
            log(f"         Rows in {region} will show as {STATUS_MISSING} regardless.")
            live_by_region[region] = {}

    report = build_report(rows, live_by_region, skipped_names)
    print_report(report, config_path)
    write_step_summary(report, config_path)

    # Reporting only — never fail the job on the strength of this view.
    return 0


# ============================================================
# MAIN
# ============================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="createapigw.py",
        description="Create WebSocket API Gateway endpoints from a CSV configuration file.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    normalize = subcommands.add_parser(
        "normalize", help="clean Domain and Region, add the generated Name column"
    )
    normalize.add_argument("--config-file", required=True, help="Path to the request CSV")
    normalize.set_defaults(func=cmd_normalize)

    check = subcommands.add_parser(
        "check", help="report names that already exist in AWS and drop those rows"
    )
    check.add_argument("--config-file", required=True, help="Path to the request CSV")
    check.add_argument(
        "--write-remaining",
        metavar="PATH",
        help="write a CSV of only the rows still to be created",
    )
    check.add_argument(
        "--fail-on-existing",
        action="store_true",
        help="exit 1 when a name already exists, instead of skipping the row",
    )
    check.set_defaults(func=cmd_check)

    cloudwatch = subcommands.add_parser(
        "cloudwatch",
        help="ensure API Gateway has an account-level CloudWatch logging role",
    )
    cloudwatch.add_argument(
        "--config-file", help="request CSV — every region in it is checked"
    )
    cloudwatch.add_argument("--region", help="check a single region instead")
    cloudwatch.add_argument(
        "--role-arn", help="use this role instead of discovering one through IAM"
    )
    cloudwatch.add_argument(
        "--require",
        action="store_true",
        help="exit 1 if a region could not be configured",
    )
    cloudwatch.set_defaults(func=cmd_cloudwatch)

    create = subcommands.add_parser(
        "create", help="create the WebSocket APIs, skipping names that already exist"
    )
    create.add_argument("--config-file", required=True, help="Path to the CSV to provision")
    create.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-API progress. Errors, skips and the tally still print",
    )
    create.set_defaults(func=cmd_create)

    report = subcommands.add_parser("report", help="print the final state of every row")
    report.add_argument("--config-file", required=True, help="The original request CSV")
    report.add_argument(
        "--skipped",
        default="",
        help="comma-separated names that already existed before this run",
    )
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

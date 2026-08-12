#!/usr/bin/env python3
"""
Create WebSocket API Gateway endpoints from a CSV configuration file.

Author:       Arnold Ramos
Date Created: 2026

Python port of CreateApiGateway.ps1 (PIM WebSocket provisioning).

For each row in the input CSV this script creates a WEBSOCKET API with three
routes ($connect, $disconnect, ping), their integrations and responses, a
"production" stage, and a deployment. The resulting WebSocket URL is appended
to pim-apigw-list.csv alongside the script.

Credentials are resolved by the standard boto3 credential chain (environment
variables, AWS_PROFILE, ~/.aws/credentials, SSO cache, container credentials,
or an EC2 instance role). Nothing is passed explicitly.

Input CSV format:
    Name,Domain,Region
    pim-example,example.com,us-east-1

    Name   - API name
    Domain - Customer domain used to build the integration URI
    Region - AWS region ID (e.g. us-east-1), not a regional group code

Usage:
    python create_api_gateway.py --config-file config.csv
"""

import argparse
import csv
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ============================================================
# CONFIGURATION
# ============================================================

ROUTE_KEYS = ["$connect", "$disconnect", "ping"]
API_STAGE = "production"
ROUTE_SELECTION_EXPRESSION = "$request.body.type"

# NOTE: the PowerShell script had this as '\$default' - the backslash was a
# literal character, not an escape, so it sent "\$default" to AWS. Corrected here.
TEMPLATE_SELECTION_EXPRESSION = "$default"

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

OUTPUT_HEADER = ["API Name", "Domain", "Websocket URL"]


# ============================================================
# HELPERS
# ============================================================

def log(message):
    """Write a message to stdout, flushed so output interleaves correctly."""
    print(message, flush=True)


def get_client(region, _cache={}):
    """Return a cached apigatewayv2 client for the given region."""
    if region not in _cache:
        _cache[region] = boto3.client("apigatewayv2", region_name=region)
    return _cache[region]


def ensure_output_file(output_path):
    """Create the output CSV with its header row if it does not already exist."""
    if not output_path.exists():
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(OUTPUT_HEADER)


def append_output_row(output_path, api_name, api_domain, api_url):
    """Append one result row, letting the csv module handle quoting."""
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([api_name, api_domain, api_url])


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
            RouteResponseSelectionExpression="$default",
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


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Create WebSocket API Gateway endpoints from a CSV configuration file."
    )
    parser.add_argument(
        "--config-file",
        required=True,
        help="Path to the CSV configuration file (columns: Name, Domain, Region)",
    )
    args = parser.parse_args()

    config_path = Path(args.config_file)
    if not config_path.is_file():
        log(f"ERROR: Configuration file not found: {config_path}")
        sys.exit(1)

    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / "pim-apigw-list.csv"
    ensure_output_file(output_path)

    with config_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        log(f"ERROR: No rows found in {config_path}")
        sys.exit(1)

    succeeded = 0
    failed = 0

    for row in rows:
        api_name = (row.get("Name") or "").strip()
        api_domain = (row.get("Domain") or "").strip()
        api_region = (row.get("Region") or "").strip()

        if not api_name:
            log(f"ERROR: Row missing Name in {config_path}. Skipping.")
            failed += 1
            continue

        # Skip the row if no Region was supplied, rather than defaulting to null
        if not api_region:
            log(f"ERROR: No Region specified for '{api_name}' in {config_path}. Skipping.")
            failed += 1
            continue

        log(f"===== Processing '{api_name}' ({api_domain}) in {api_region} =====")
        try:
            api_url = provision_api(api_name, api_domain, api_region)
            append_output_row(output_path, api_name, api_domain, api_url)
            log(f"SUCCESS: '{api_name}' -> {api_url}")
            succeeded += 1
        except (ClientError, BotoCoreError, RuntimeError) as error:
            # A failure here may leave a partially built API in AWS - review before re-running,
            # since API Gateway permits duplicate API names and a retry will not resume.
            log(f"ERROR: Failed to create '{api_name}' in {api_region}: {error}")
            failed += 1

    log(f"\nDone. {succeeded} succeeded, {failed} failed.")
    log(f"Output written to {output_path}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
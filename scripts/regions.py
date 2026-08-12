"""The AWS regions a request may target.

normalize_requests.py lowercases the Region column; validate_requests.py checks
the result is in this list. Both import from here so the two cannot disagree.

Region IDs only — us-east-1, not a group code like use1 and not a display name
like "US East (N. Virginia)". To allow another region, add it below.
"""

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


def normalize_region(value):
    """Lowercase and trim a Region cell. AWS region IDs are always lowercase."""
    return (value or "").strip().lower()


def is_allowed(value):
    """True when the value names an allowed region, whatever its casing."""
    return normalize_region(value) in ALLOWED_REGIONS

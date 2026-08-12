"""The API naming rule, in one place.

APIs are named:

    <prefix><pod number>api-<first label of the domain>

where the prefix comes from the Environment column and the pod number is the
Pod column verbatim — that column holds digits only:

    prod / production   -> pdpm      Pod 1 + example.com -> pdpm1api-example
    dev  / development  -> dvpm      Pod 1 + example.com -> dvpm1api-example

normalize_requests.py applies this rule to the CSV; validate_requests.py checks
it held. Both import from here so the rule cannot drift between them.

An Environment not listed in ENVIRONMENT_PREFIXES keeps whatever Name the
requester supplied — add it below to bring it under the rule.
"""

import re

ENVIRONMENT_PREFIXES = {
    "prod": "pdpm",
    "production": "pdpm",
    "dev": "dvpm",
    "development": "dvpm",
}

# The Pod column holds the number and nothing else — "1", not "pod1".
POD_NUMBER = re.compile(r"^\d+$")


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

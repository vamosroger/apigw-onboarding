"""Guards on the API Gateway selection expressions.

These constants were "corrected" once during the PowerShell port by removing a
backslash that looked like an escaping artefact. It was not — API Gateway needs
it — and CreateIntegration failed with:

    Unexpected variable in selection expression: $default

after creating the API but before finishing it, leaving an orphan behind. These
tests exist so that never happens silently again.
"""

from createapigw import (
    INTEGRATION_RESPONSE_TEMPLATE_SELECTION,
    REQUEST_TEMPLATES,
    ROUTE_KEYS,
    ROUTE_RESPONSE_SELECTION_EXPRESSION,
    ROUTE_SELECTION_EXPRESSION,
    TEMPLATE_SELECTION_EXPRESSION,
)


def test_template_selection_expression_escapes_the_dollar():
    # The literal string sent to AWS must be \$default, not $default.
    assert TEMPLATE_SELECTION_EXPRESSION == r"\$default"


def test_route_response_selection_expression_escapes_the_dollar():
    assert ROUTE_RESPONSE_SELECTION_EXPRESSION == r"\$default"


def test_genuine_variable_expressions_are_not_escaped():
    # These reference real variables, so the $ must stay unescaped.
    assert ROUTE_SELECTION_EXPRESSION == "$request.body.type"
    assert INTEGRATION_RESPONSE_TEMPLATE_SELECTION == "${integration.response.statuscode}"
    assert "\\" not in ROUTE_SELECTION_EXPRESSION
    assert "\\" not in INTEGRATION_RESPONSE_TEMPLATE_SELECTION


def test_request_template_keys_are_unescaped():
    # These are keys matched against the expression's result, not expressions
    # themselves, so they hold the plain reserved word.
    for route_key, templates in REQUEST_TEMPLATES.items():
        assert list(templates) == ["$default"], route_key


def test_every_route_has_a_request_template():
    assert set(REQUEST_TEMPLATES) == set(ROUTE_KEYS)

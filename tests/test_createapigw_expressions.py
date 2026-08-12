"""Guards on the API Gateway selection expressions.

Escaping here is inconsistent between operations, and each variant has cost a
failed run and an orphaned API. Both rules are confirmed against live AWS:

    CreateIntegration  TemplateSelectionExpression      needs "\\$default"
        unescaped ->  Unexpected variable in selection expression: $default

    CreateRoute        RouteResponseSelectionExpression needs "$default"
        escaped   ->  Currently, only $default is supported as a route
                      response selection expression.

Do not "tidy" these into agreeing with each other. They do not agree.
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
    # CreateIntegration: the literal sent to AWS must be \$default.
    assert TEMPLATE_SELECTION_EXPRESSION == r"\$default"


def test_route_response_selection_expression_does_not_escape_the_dollar():
    # CreateRoute: the opposite. AWS rejects \$default here.
    assert ROUTE_RESPONSE_SELECTION_EXPRESSION == "$default"


def test_the_two_selection_expressions_deliberately_disagree():
    # Guards against someone making them consistent. One run each proved both.
    assert TEMPLATE_SELECTION_EXPRESSION != ROUTE_RESPONSE_SELECTION_EXPRESSION


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

"""Tests for the account-level CloudWatch logging role check.

Only the pure decision logic is covered — the get_account / update_account /
list_roles calls are not exercised against AWS.
"""

import json

from createapigw import APIGW_CLOUDWATCH_POLICY, choose_role, trusts_apigateway

APIGW_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "apigateway.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}


def test_a_policy_trusting_api_gateway_is_recognised():
    assert trusts_apigateway(APIGW_TRUST) is True


def test_a_policy_trusting_another_service_is_not():
    assert trusts_apigateway(LAMBDA_TRUST) is False


def test_a_service_list_is_handled():
    policy = {"Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": ["lambda.amazonaws.com", "apigateway.amazonaws.com"]},
    }]}
    assert trusts_apigateway(policy) is True


def test_a_deny_statement_does_not_count_as_trust():
    policy = {"Statement": [{
        "Effect": "Deny",
        "Principal": {"Service": "apigateway.amazonaws.com"},
    }]}
    assert trusts_apigateway(policy) is False


def test_a_json_string_policy_is_accepted():
    # boto3 normally decodes this, but do not depend on it.
    assert trusts_apigateway(json.dumps(APIGW_TRUST)) is True


def test_junk_policies_are_rejected_rather_than_raising():
    for value in [
        None, "", "not json", 42, [],
        {"Statement": "nonsense"},          # Statement is not a list
        {"Statement": ["nonsense"]},        # a statement is not a dict
        {"Statement": [{"Effect": "Allow", "Principal": "everyone"}]},
        {"Statement": [{"Effect": "Allow", "Principal": {"Service": 7}}]},
    ]:
        assert trusts_apigateway(value) is False, value


def test_a_single_statement_object_is_accepted():
    # A one-statement policy is sometimes written without the list.
    policy = {"Statement": {
        "Effect": "Allow",
        "Principal": {"Service": "apigateway.amazonaws.com"},
    }}
    assert trusts_apigateway(policy) is True


def test_no_candidates_means_no_choice():
    arn, reason = choose_role([])
    assert arn is None
    assert "no IAM role" in reason


def test_the_only_role_is_used():
    arn, reason = choose_role([("ApiGwRole", "arn:aws:iam::1:role/ApiGwRole", False)])
    assert arn == "arn:aws:iam::1:role/ApiGwRole"
    assert "only role" in reason


def test_the_role_with_the_managed_policy_wins():
    arn, reason = choose_role([
        ("Other", "arn:aws:iam::1:role/Other", False),
        ("Logs", "arn:aws:iam::1:role/Logs", True),
    ])
    assert arn == "arn:aws:iam::1:role/Logs"
    assert APIGW_CLOUDWATCH_POLICY in reason


def test_two_roles_with_the_policy_is_refused():
    # This grants a role account-wide; guessing is worse than doing nothing.
    arn, reason = choose_role([
        ("LogsA", "arn:aws:iam::1:role/LogsA", True),
        ("LogsB", "arn:aws:iam::1:role/LogsB", True),
    ])
    assert arn is None
    assert "--role-arn" in reason


def test_two_roles_without_the_policy_is_refused():
    arn, reason = choose_role([
        ("A", "arn:aws:iam::1:role/A", False),
        ("B", "arn:aws:iam::1:role/B", False),
    ])
    assert arn is None
    assert "--role-arn" in reason

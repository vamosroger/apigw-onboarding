from naming import derive_name, domain_label, environment_prefix, is_derived, pod_number


def test_prod_uses_the_pdpm_prefix():
    assert derive_name("prod", "example.com", "1") == "pdpm1api-example"


def test_production_is_treated_as_prod():
    assert derive_name("production", "example.com", "1") == "pdpm1api-example"


def test_dev_uses_the_dvpm_prefix():
    assert derive_name("dev", "example.com", "1") == "dvpm1api-example"


def test_development_is_treated_as_dev():
    assert derive_name("development", "example.com", "1") == "dvpm1api-example"


def test_environment_matching_is_case_insensitive():
    assert derive_name("PROD", "example.com", "1") == "pdpm1api-example"
    assert derive_name("  Development  ", "example.com", "1") == "dvpm1api-example"


def test_only_the_first_domain_label_is_used():
    assert derive_name("prod", "pim.example.com", "1") == "pdpm1api-pim"
    assert derive_name("prod", "api.eu.example.com", "1") == "pdpm1api-api"


def test_the_generated_name_never_contains_a_dot():
    for domain in ["example.com", "pim.example.com", "api.eu.example.co.uk"]:
        assert "." not in derive_name("prod", domain, "1")


def test_the_generated_name_is_lowercase():
    assert derive_name("prod", "PIM.Example.COM", "1") == "pdpm1api-pim"


def test_pod_number_accepts_digits_only():
    assert pod_number("1") == "1"
    assert pod_number("12") == "12"
    assert pod_number("03") == "03"     # verbatim: leading zeros are kept
    assert pod_number(" 7 ") == "7"     # surrounding whitespace is tolerated


def test_pod_number_rejects_anything_that_is_not_digits():
    for value in ["pod1", "POD 1", "1a", "pod-03", "", "   ", None]:
        assert pod_number(value) == "", value


def test_an_unlisted_environment_is_not_derived():
    # Staging has no prefix, so the supplied Name stands.
    assert derive_name("staging", "example.com", "1") == ""
    assert is_derived("staging") is False
    assert environment_prefix("staging") == ""


def test_is_derived_covers_the_listed_environments():
    for environment in ["prod", "production", "dev", "development", "PROD"]:
        assert is_derived(environment) is True


def test_derivation_fails_closed_on_missing_pieces():
    assert derive_name("prod", "example.com", "") == ""     # no pod number
    assert derive_name("prod", "", "1") == ""            # no domain
    assert derive_name("", "example.com", "1") == ""     # no environment


def test_domain_label_expects_a_bare_host():
    # A scheme survives, which is why normalize_domain must run first.
    assert domain_label("https://example.com") == "https://example"
    assert domain_label("example.com") == "example"

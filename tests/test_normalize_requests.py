import csv

from normalize_requests import main, normalize_domain, normalize_rows


def test_https_prefix_is_stripped():
    assert normalize_domain("https://example.com") == "example.com"


def test_http_prefix_is_stripped():
    assert normalize_domain("http://example.com") == "example.com"


def test_scheme_matching_is_case_insensitive():
    assert normalize_domain("HTTPS://Example.com") == "Example.com"


def test_other_schemes_are_stripped():
    # wss:// is an easy thing to type for a WebSocket endpoint.
    assert normalize_domain("wss://example.com") == "example.com"


def test_bare_domain_is_unchanged():
    assert normalize_domain("example.com") == "example.com"


def test_trailing_slash_is_removed():
    assert normalize_domain("https://example.com/") == "example.com"


def test_surrounding_whitespace_is_removed():
    assert normalize_domain("  https://example.com  ") == "example.com"


def test_subdomain_is_preserved():
    assert normalize_domain("https://api.eu.example.com") == "api.eu.example.com"


def test_empty_value_stays_empty():
    # validate_requests.py reports this as an error; normalising must not mask it.
    assert normalize_domain("") == ""
    assert normalize_domain("   ") == ""


def test_a_domain_containing_https_in_its_name_is_not_mangled():
    assert normalize_domain("https-test.example.com") == "https-test.example.com"


def test_normalize_rows_reports_only_changed_rows():
    rows = [
        {"Name": "a", "Domain": "https://a.com", "Region": "us-east-1"},
        {"Name": "b", "Domain": "b.com", "Region": "us-east-1"},
    ]
    changes = normalize_rows(rows)
    assert changes == [(2, "Domain", "https://a.com", "a.com")]
    assert rows[0]["Domain"] == "a.com"
    assert rows[1]["Domain"] == "b.com"


def test_prod_name_is_generated():
    rows = [{"Name": "", "Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert rows[0]["Name"] == "pdpm1api-example"
    assert (2, "Name", "", "pdpm1api-example") in changes


def test_dev_name_is_generated():
    rows = [{"Name": "", "Domain": "example.com", "Region": "us-east-1",
             "Environment": "dev", "Pod": "2"}]
    normalize_rows(rows)
    assert rows[0]["Name"] == "dvpm2api-example"


def test_a_hand_typed_name_is_overwritten():
    rows = [{"Name": "whatever-they-typed", "Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert rows[0]["Name"] == "pdpm1api-example"
    assert (2, "Name", "whatever-they-typed", "pdpm1api-example") in changes


def test_the_name_is_derived_from_the_cleaned_domain():
    # Domain is normalised first, so the scheme never reaches the name rule.
    rows = [{"Name": "", "Domain": "https://pim.example.com/", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    normalize_rows(rows)
    assert rows[0]["Domain"] == "pim.example.com"
    assert rows[0]["Name"] == "pdpm1api-pim"


def test_an_unlisted_environment_keeps_its_supplied_name():
    rows = [{"Name": "keep-me", "Domain": "example.com", "Region": "us-east-1",
             "Environment": "staging", "Pod": "1"}]
    assert normalize_rows(rows) == []
    assert rows[0]["Name"] == "keep-me"


def test_an_underivable_prod_row_keeps_its_name_for_the_validator_to_catch():
    rows = [{"Name": "keep-me", "Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": ""}]
    assert normalize_rows(rows) == []
    assert rows[0]["Name"] == "keep-me"


def test_main_rewrites_the_file(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(
        "Name,Domain,Region\npim-example,https://example.com/,us-east-1\n", encoding="utf-8"
    )
    assert main(["--config-file", str(csv_file)]) == 0

    with csv_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Domain"] == "example.com"
    assert rows[0]["Name"] == "pim-example"
    assert rows[0]["Region"] == "us-east-1"


def test_main_preserves_extra_columns(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(
        "Name,Domain,Region,CR\npim-example,https://example.com,us-east-1,CHG0012345\n",
        encoding="utf-8",
    )
    assert main(["--config-file", str(csv_file)]) == 0

    with csv_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["CR"] == "CHG0012345"
    assert rows[0]["Domain"] == "example.com"


def test_main_leaves_a_clean_file_untouched(tmp_path):
    csv_file = tmp_path / "requests.csv"
    original = "Name,Domain,Region\npim-example,example.com,us-east-1\n"
    csv_file.write_text(original, encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0
    assert csv_file.read_text(encoding="utf-8") == original


def test_main_tolerates_a_missing_domain_column(tmp_path):
    # validate_requests.py owns that error; this must not crash first.
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Name,Region\npim-example,us-east-1\n", encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0


def test_main_rejects_a_missing_file(tmp_path):
    assert main(["--config-file", str(tmp_path / "nope.csv")]) == 1

import csv

from createapigw import cmd_normalize, normalize_domain, normalize_rows


class _Args:
    """Stand-in for the parsed argparse namespace."""
    def __init__(self, config_file):
        self.config_file = config_file


def main(argv):
    # Tests call main(["--config-file", path]); route that to the subcommand.
    return cmd_normalize(_Args(argv[argv.index("--config-file") + 1]))


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


def test_only_changed_values_are_reported():
    rows = [
        {"Domain": "https://a.com", "Region": "us-east-1", "Environment": "staging", "Pod": "1"},
        {"Domain": "b.com", "Region": "us-east-1", "Environment": "staging", "Pod": "1"},
    ]
    changes = normalize_rows(rows)
    # staging has no naming rule, so no Name is generated for either row.
    assert changes == [(2, "Domain", "https://a.com", "a.com")]
    assert rows[0]["Domain"] == "a.com"
    assert rows[1]["Domain"] == "b.com"


def test_prod_name_is_generated():
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert rows[0]["Name"] == "pdpm1api-example"
    assert (2, "Name", "", "pdpm1api-example") in changes


def test_dev_name_is_generated():
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "dev", "Pod": "2"}]
    normalize_rows(rows)
    assert rows[0]["Name"] == "dvpm2api-example"


def test_a_name_column_is_added_to_rows_that_lack_one():
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert rows[0]["Name"] == "pdpm1api-example"
    assert (2, "Name", "", "pdpm1api-example") in changes


def test_the_name_is_derived_from_the_cleaned_domain():
    # Domain is normalised first, so the scheme never reaches the name rule.
    rows = [{"Domain": "https://pim.example.com/", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    normalize_rows(rows)
    assert rows[0]["Domain"] == "pim.example.com"
    assert rows[0]["Name"] == "pdpm1api-pim"


def test_an_unlisted_environment_gets_an_empty_name():
    # validate_requests.py rejects the row; normalising must not invent a name.
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "staging", "Pod": "1"}]
    assert normalize_rows(rows) == []
    assert rows[0]["Name"] == ""


def test_an_underivable_prod_row_gets_an_empty_name():
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": ""}]
    assert normalize_rows(rows) == []
    assert rows[0]["Name"] == ""


def test_region_is_lowercased():
    rows = [{"Domain": "example.com", "Region": "US-EAST-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert rows[0]["Region"] == "us-east-1"
    assert (2, "Region", "US-EAST-1", "us-east-1") in changes


def test_an_already_lowercase_region_is_left_alone():
    rows = [{"Domain": "example.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "1"}]
    changes = normalize_rows(rows)
    assert not any(field == "Region" for _, field, _, _ in changes)


def test_main_adds_the_name_column(tmp_path):
    # A request CSV as authored: no Name column at all.
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(
        "Domain,Region,Environment,Pod\nhttps://example.com/,us-east-1,prod,1\n",
        encoding="utf-8",
    )
    assert main(["--config-file", str(csv_file)]) == 0

    with csv_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames[0] == "Name"   # prepended
        rows = list(reader)
    assert rows[0]["Name"] == "pdpm1api-example"
    assert rows[0]["Domain"] == "example.com"
    assert rows[0]["Region"] == "us-east-1"


def test_main_preserves_extra_columns(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(
        "Domain,Region,Environment,Pod,CR\nhttps://example.com,us-east-1,prod,1,CHG0012345\n",
        encoding="utf-8",
    )
    assert main(["--config-file", str(csv_file)]) == 0

    with csv_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["CR"] == "CHG0012345"
    assert rows[0]["Domain"] == "example.com"
    assert rows[0]["Name"] == "pdpm1api-example"


def test_main_is_idempotent(tmp_path):
    # Running twice must not change anything the second time.
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(
        "Domain,Region,Environment,Pod\nexample.com,us-east-1,prod,1\n", encoding="utf-8"
    )
    assert main(["--config-file", str(csv_file)]) == 0
    once = csv_file.read_text(encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0
    assert csv_file.read_text(encoding="utf-8") == once


def test_main_tolerates_a_missing_domain_column(tmp_path):
    # validate_requests.py owns that error; this must not crash first.
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Region,Environment,Pod\nus-east-1,prod,1\n", encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0


def test_main_rejects_a_missing_file(tmp_path):
    assert main(["--config-file", str(tmp_path / "nope.csv")]) == 1

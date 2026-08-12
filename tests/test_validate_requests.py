import validate_requests
from validate_requests import main, validate_rows

HEADER = "Name,Domain,Region,Environment,Pod\n"
GOOD_ROW = "pdpm1api-example,example.com,us-east-1,prod,1\n"


def row(name="pim-example", domain="example.com", region="us-east-1",
        environment="staging", pod="1"):
    """A valid row.

    Environment defaults to staging — which has no naming rule — so tests of
    unrelated fields are not tripped up by the derived-Name check. Tests that
    care about derivation set it explicitly.
    """
    return {
        "Name": name,
        "Domain": domain,
        "Region": region,
        "Environment": environment,
        "Pod": pod,
    }


def test_valid_rows_produce_no_errors():
    assert validate_rows([row(), row(name="pim-other")]) == []


def test_missing_name_is_reported():
    errors = validate_rows([row(name="")])
    assert any("Name is empty" in error for error in errors)


def test_missing_domain_is_reported():
    errors = validate_rows([row(domain="  ")])
    assert any("Domain is empty" in error for error in errors)


def test_missing_region_is_reported():
    errors = validate_rows([row(region="")])
    assert any("Region is empty" in error for error in errors)


def test_missing_environment_is_reported():
    errors = validate_rows([row(environment="")])
    assert any("Environment is empty" in error for error in errors)


def test_missing_pod_is_reported():
    errors = validate_rows([row(pod="   ")])
    assert any("Pod is empty" in error for error in errors)


def test_pod_must_be_digits_only():
    for value in ["pod1", "POD 1", "1a", "pod-03"]:
        errors = validate_rows([row(pod=value)])
        assert any("must be digits only" in error for error in errors), value


def test_a_digits_only_pod_is_accepted():
    assert validate_rows([row(pod="12")]) == []
    assert validate_rows([row(pod="03")]) == []


def test_any_environment_is_accepted_by_default():
    # ALLOWED_ENVIRONMENTS is empty, so presence is all that is checked.
    assert validate_rows([row(environment="sandbox-eu")]) == []


def test_environment_is_restricted_when_a_list_is_configured(monkeypatch):
    monkeypatch.setattr(validate_requests, "ALLOWED_ENVIRONMENTS", ["dev", "prod"])
    assert validate_rows([row(environment="prod", name="pdpm1api-example")]) == []
    errors = validate_rows([row(environment="sandbox")])
    assert any("is not one of: dev, prod" in error for error in errors)


def test_a_prod_row_must_use_the_derived_name():
    errors = validate_rows([row(environment="prod", name="pim-example")])
    assert any("should be 'pdpm1api-example'" in error for error in errors)


def test_a_prod_row_with_the_derived_name_passes():
    assert validate_rows([row(environment="prod", name="pdpm1api-example")]) == []


def test_a_dev_row_must_use_the_dvpm_name():
    errors = validate_rows([row(environment="dev", name="pdpm1api-example")])
    assert any("should be 'dvpm1api-example'" in error for error in errors)


def test_the_derived_name_uses_the_first_domain_label():
    assert validate_rows(
        [row(environment="prod", domain="pim.example.com", name="pdpm1api-pim")]
    ) == []


def test_an_unlisted_environment_keeps_its_supplied_name():
    assert validate_rows([row(environment="staging", name="anything-goes")]) == []


def test_a_bad_pod_does_not_also_raise_a_name_error():
    # Derivation fails closed, so the row gets one clear error, not two.
    errors = validate_rows([row(environment="prod", pod="pod1")])
    assert any("must be digits only" in error for error in errors)
    assert not any("should be" in error for error in errors)


def test_regional_group_code_is_rejected():
    # 'use1' is a group code, not a region ID — the exact mistake the script's
    # docstring warns about.
    errors = validate_rows([row(region="use1")])
    assert any("not a valid AWS region ID" in error for error in errors)


def test_duplicate_names_are_reported():
    errors = validate_rows([row(), row()])
    assert any("duplicate Name" in error for error in errors)


def test_row_numbers_account_for_the_header():
    errors = validate_rows([row(name=""), row(domain="")])
    assert any(error.startswith("row 2:") for error in errors)
    assert any(error.startswith("row 3:") for error in errors)


def test_main_accepts_a_good_csv(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(HEADER + GOOD_ROW, encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0


def test_main_rejects_a_file_missing_the_new_columns(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Name,Domain,Region\npim-example,example.com,us-east-1\n", encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 1


def test_main_rejects_a_missing_column(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Name,Domain\npim-example,example.com\n", encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 1


def test_main_rejects_an_empty_file(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(HEADER, encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 1


def test_main_rejects_a_missing_file(tmp_path):
    assert main(["--config-file", str(tmp_path / "nope.csv")]) == 1


def test_the_shipped_template_is_valid():
    # The template is what people copy — it must pass its own validator.
    assert main(["--config-file", "requests/example.csv.template"]) == 0

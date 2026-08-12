from validate_requests import main, validate_rows

HEADER = "Domain,Region,Environment,Pod\n"
GOOD_ROW = "example.com,us-east-1,prod,1\n"


def row(domain="example.com", region="us-east-1", environment="prod", pod="1"):
    """A valid row. There is no Name column — the name is generated."""
    return {
        "Domain": domain,
        "Region": region,
        "Environment": environment,
        "Pod": pod,
    }


def test_valid_rows_produce_no_errors():
    assert validate_rows([row(), row(domain="other.com")]) == []


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


def test_an_environment_without_a_naming_rule_is_rejected():
    # With no Name column there is nothing to fall back on, so this must fail.
    errors = validate_rows([row(environment="staging")])
    assert any("has no naming rule" in error for error in errors)
    assert any("naming.py" in error for error in errors)


def test_the_listed_environments_are_accepted():
    for environment in ["prod", "production", "dev", "development", "PROD"]:
        assert validate_rows([row(environment=environment)]) == [], environment


def test_regional_group_code_is_rejected():
    # 'use1' is a group code, not a region ID.
    errors = validate_rows([row(region="use1")])
    assert any("is not an allowed region" in error for error in errors)


def test_a_region_outside_the_allowed_list_is_rejected():
    errors = validate_rows([row(region="us-gov-west-1")])
    assert any("is not an allowed region" in error for error in errors)
    assert any("regions.py" in error for error in errors)


def test_region_is_accepted_whatever_its_casing():
    # validate runs before and after normalize, so both must pass.
    assert validate_rows([row(region="US-EAST-1")]) == []
    assert validate_rows([row(region="  Us-East-1  ")]) == []


def test_rows_generating_the_same_name_are_reported():
    # Same environment, pod and first domain label -> same generated name.
    errors = validate_rows([row(domain="pim.a.com"), row(domain="pim.b.com")])
    assert any("would generate 'pdpm1api-pim'" in error for error in errors)


def test_different_pods_do_not_collide():
    assert validate_rows([row(pod="1"), row(pod="2")]) == []


def test_row_numbers_account_for_the_header():
    errors = validate_rows([row(domain=""), row(region="")])
    assert any(error.startswith("row 2:") for error in errors)
    assert any(error.startswith("row 3:") for error in errors)


def test_a_name_column_added_by_normalize_must_agree():
    good = row()
    good["Name"] = "pdpm1api-example"
    assert validate_rows([good]) == []

    bad = row()
    bad["Name"] = "hand-typed"
    errors = validate_rows([bad])
    assert any("should be 'pdpm1api-example'" in error for error in errors)


def test_a_raw_csv_without_a_name_column_is_not_penalised():
    # validate_requests.py is also run by hand before opening a PR.
    assert "Name" not in row()
    assert validate_rows([row()]) == []


def test_main_accepts_a_good_csv(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text(HEADER + GOOD_ROW, encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 0


def test_main_rejects_a_file_that_still_has_the_old_header(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Name,Domain,Region\npim-example,example.com,us-east-1\n", encoding="utf-8")
    assert main(["--config-file", str(csv_file)]) == 1


def test_main_rejects_a_missing_column(tmp_path):
    csv_file = tmp_path / "requests.csv"
    csv_file.write_text("Domain,Region\nexample.com,us-east-1\n", encoding="utf-8")
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

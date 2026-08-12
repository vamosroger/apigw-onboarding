from createapigw import (
    STATUS_CREATED,
    STATUS_EXISTING,
    STATUS_MISSING,
    build_report,
    counts,
    write_step_summary,
)


def api(api_id="abc123", endpoint="wss://abc123.execute-api.us-east-1.amazonaws.com"):
    return {"ApiId": api_id, "ApiEndpoint": endpoint}


def row(name="pdpm1api-pim", region="us-east-1"):
    return {"Name": name, "Region": region, "Domain": "pim.acme.com"}


def test_a_skipped_name_is_reported_as_existing():
    report = build_report(
        [row()], {"us-east-1": {"pdpm1api-pim": api()}}, {"pdpm1api-pim"}
    )
    assert report == [(STATUS_EXISTING, "pdpm1api-pim", "us-east-1", "abc123",
                       "wss://abc123.execute-api.us-east-1.amazonaws.com")]


def test_a_name_not_skipped_but_present_is_reported_as_created():
    report = build_report([row()], {"us-east-1": {"pdpm1api-pim": api()}}, set())
    assert report[0][0] == STATUS_CREATED


def test_a_name_absent_from_aws_is_reported_as_missing():
    # The row failed — this is the case the earlier steps did not surface.
    report = build_report([row()], {"us-east-1": {}}, set())
    assert report[0] == (STATUS_MISSING, "pdpm1api-pim", "us-east-1", "-", "-")


def test_a_half_built_api_still_shows_as_created():
    # create_api succeeded, a later call failed. AWS has it, so it is reported
    # rather than silently absent.
    report = build_report([row()], {"us-east-1": {"pdpm1api-pim": api("do5h3hlxg8")}}, set())
    assert report[0][0] == STATUS_CREATED
    assert report[0][3] == "do5h3hlxg8"


def test_region_is_matched_case_insensitively():
    report = build_report(
        [row(region="US-EAST-1")], {"us-east-1": {"pdpm1api-pim": api()}}, set()
    )
    assert report[0][0] == STATUS_CREATED
    assert report[0][2] == "us-east-1"


def test_the_same_name_in_another_region_is_not_matched():
    report = build_report(
        [row(region="eu-west-1")], {"us-east-1": {"pdpm1api-pim": api()}}, set()
    )
    assert report[0][0] == STATUS_MISSING


def test_rows_without_a_name_are_skipped():
    assert build_report([row(name="")], {}, set()) == []


def test_a_region_whose_lookup_failed_yields_missing():
    # fetch_existing downgrades an AWS error to an empty dict.
    report = build_report([row()], {"us-east-1": {}}, {"pdpm1api-pim"})
    assert report[0][0] == STATUS_MISSING


def test_counts_tallies_each_status():
    report = [
        (STATUS_EXISTING, "a", "us-east-1", "1", "u"),
        (STATUS_CREATED, "b", "us-east-1", "2", "u"),
        (STATUS_CREATED, "c", "us-east-1", "3", "u"),
        (STATUS_MISSING, "d", "us-east-1", "-", "-"),
    ]
    assert counts(report) == (1, 2, 1)


def test_step_summary_lists_every_row(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = [
        (STATUS_EXISTING, "pdpm1api-pim", "us-east-1", "abc123", "wss://a"),
        (STATUS_CREATED, "dvpm3api-jdaco", "us-east-1", "def456", "wss://b"),
        (STATUS_MISSING, "dvpm4api-shop", "eu-west-1", "-", "-"),
    ]
    write_step_summary(report, "requests/pim_apigw_CHG1234567.csv")
    written = summary.read_text(encoding="utf-8")
    for needle in ["pdpm1api-pim", "dvpm3api-jdaco", "dvpm4api-shop",
                   "1 already existed, 1 created, 1 missing"]:
        assert needle in written, needle


def test_step_summary_is_a_noop_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    write_step_summary([(STATUS_CREATED, "a", "us-east-1", "1", "u")], "x.csv")

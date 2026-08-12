import csv

from check_existing import (
    describe,
    find_collisions,
    partition_rows,
    report,
    rows_by_region,
    write_remaining,
)


def api(api_id="abc123", name="pdpm1api-pim", endpoint="wss://abc123.execute-api.us-east-1.amazonaws.com"):
    return {"ApiId": api_id, "Name": name, "ProtocolType": "WEBSOCKET", "ApiEndpoint": endpoint}


def test_rows_are_grouped_by_region():
    rows = [
        {"Name": "a", "Domain": "a.com", "Region": "us-east-1"},
        {"Name": "b", "Domain": "b.com", "Region": "us-east-1"},
        {"Name": "c", "Domain": "c.com", "Region": "eu-west-1"},
    ]
    assert rows_by_region(rows) == {
        "us-east-1": [("a", "a.com"), ("b", "b.com")],
        "eu-west-1": [("c", "c.com")],
    }


def test_rows_missing_a_name_or_region_are_skipped():
    # validate_requests.py reports those; this must not crash on them.
    rows = [
        {"Name": "", "Domain": "a.com", "Region": "us-east-1"},
        {"Name": "b", "Domain": "b.com", "Region": ""},
        {"Name": "c", "Domain": "c.com", "Region": "us-east-1"},
    ]
    assert rows_by_region(rows) == {"us-east-1": [("c", "c.com")]}


def test_no_collision_when_the_name_is_new():
    requested = {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]}
    existing = {"us-east-1": {"something-else": [api(name="something-else")]}}
    assert find_collisions(requested, existing) == []


def test_a_collision_is_found():
    requested = {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]}
    existing = {"us-east-1": {"pdpm1api-pim": [api()]}}
    hits = find_collisions(requested, existing)
    assert len(hits) == 1
    assert hits[0]["name"] == "pdpm1api-pim"
    assert hits[0]["region"] == "us-east-1"
    assert hits[0]["existing"][0]["ApiId"] == "abc123"


def test_the_same_name_in_a_different_region_is_not_a_collision():
    requested = {"eu-west-1": [("pdpm1api-pim", "pim.acme.com")]}
    existing = {"us-east-1": {"pdpm1api-pim": [api()]}}
    assert find_collisions(requested, existing) == []


def test_a_region_with_no_data_is_treated_as_empty():
    # Happens when the get_apis call failed and was downgraded to a warning.
    requested = {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]}
    assert find_collisions(requested, {}) == []


def test_multiple_pre_existing_apis_with_the_same_name_are_all_reported():
    requested = {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]}
    existing = {"us-east-1": {"pdpm1api-pim": [api(api_id="one"), api(api_id="two")]}}
    hits = find_collisions(requested, existing)
    assert [a["ApiId"] for a in hits[0]["existing"]] == ["one", "two"]


def test_name_matching_is_exact():
    # AWS treats these as different names, so this script must too.
    requested = {"us-east-1": [("PDPM1API-PIM", "pim.acme.com")]}
    existing = {"us-east-1": {"pdpm1api-pim": [api()]}}
    assert find_collisions(requested, existing) == []


def test_describe_survives_a_sparse_api_object():
    assert describe({}) == "ApiId=? protocol=? endpoint=?"


def test_report_writes_the_step_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    hits = find_collisions(
        {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]},
        {"us-east-1": {"pdpm1api-pim": [api()]}},
    )
    report(hits, "requests/pim_apigw_CHG1234567.csv")
    written = summary.read_text(encoding="utf-8")
    assert "pdpm1api-pim" in written
    assert "abc123" in written


def test_existing_rows_are_dropped_from_the_work_list():
    rows = [
        {"Name": "pdpm1api-pim", "Region": "us-east-1", "Domain": "pim.acme.com"},
        {"Name": "pdpm2api-new", "Region": "us-east-1", "Domain": "new.acme.com"},
    ]
    collisions = find_collisions(
        rows_by_region(rows), {"us-east-1": {"pdpm1api-pim": [api()]}}
    )
    to_create, skipped = partition_rows(rows, collisions)
    assert [r["Name"] for r in to_create] == ["pdpm2api-new"]
    assert [r["Name"] for r in skipped] == ["pdpm1api-pim"]


def test_nothing_is_dropped_when_no_name_exists():
    rows = [{"Name": "pdpm2api-new", "Region": "us-east-1", "Domain": "new.acme.com"}]
    to_create, skipped = partition_rows(rows, [])
    assert to_create == rows
    assert skipped == []


def test_every_row_can_be_skipped():
    rows = [{"Name": "pdpm1api-pim", "Region": "us-east-1", "Domain": "pim.acme.com"}]
    collisions = find_collisions(
        rows_by_region(rows), {"us-east-1": {"pdpm1api-pim": [api()]}}
    )
    to_create, skipped = partition_rows(rows, collisions)
    assert to_create == []
    assert len(skipped) == 1


def test_the_same_name_in_another_region_is_still_created():
    rows = [
        {"Name": "pdpm1api-pim", "Region": "us-east-1", "Domain": "pim.acme.com"},
        {"Name": "pdpm1api-pim", "Region": "eu-west-1", "Domain": "pim.acme.com"},
    ]
    collisions = find_collisions(
        rows_by_region(rows), {"us-east-1": {"pdpm1api-pim": [api()]}}
    )
    to_create, skipped = partition_rows(rows, collisions)
    assert [r["Region"] for r in to_create] == ["eu-west-1"]
    assert [r["Region"] for r in skipped] == ["us-east-1"]


def test_write_remaining_round_trips(tmp_path):
    out = tmp_path / "to_create.csv"
    fields = ["Name", "Domain", "Region", "Environment", "Pod"]
    rows = [{"Name": "pdpm2api-new", "Domain": "new.acme.com", "Region": "us-east-1",
             "Environment": "prod", "Pod": "2"}]
    write_remaining(str(out), fields, rows)
    with out.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == rows


def test_write_remaining_still_writes_a_header_when_empty(tmp_path):
    # createapigw.py is skipped in this case, but the file must be well-formed.
    out = tmp_path / "to_create.csv"
    write_remaining(str(out), ["Name", "Domain", "Region"], [])
    assert out.read_text(encoding="utf-8").strip() == "Name,Domain,Region"


def test_report_is_a_noop_without_a_summary_file(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    hits = find_collisions(
        {"us-east-1": [("pdpm1api-pim", "pim.acme.com")]},
        {"us-east-1": {"pdpm1api-pim": [api()]}},
    )
    report(hits, "requests/pim_apigw_CHG1234567.csv")  # must not raise

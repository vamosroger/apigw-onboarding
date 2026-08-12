from createapigw import ALLOWED_REGIONS, is_allowed, normalize_region


def test_the_expected_regions_are_allowed():
    assert len(ALLOWED_REGIONS) == 34


def test_every_entry_is_a_lowercase_region_id():
    for region in ALLOWED_REGIONS:
        assert region == region.lower(), region
        assert region.count("-") >= 2, region
        assert region.rsplit("-", 1)[1].isdigit(), region


def test_a_sample_from_each_geography_is_allowed():
    for region in [
        "us-east-1", "us-west-2", "af-south-1", "ap-southeast-7", "ap-east-2",
        "ca-west-1", "eu-central-2", "il-central-1", "mx-central-1",
        "me-central-1", "sa-east-1",
    ]:
        assert is_allowed(region), region


def test_matching_ignores_case():
    assert is_allowed("US-EAST-1")
    assert is_allowed("Us-East-1")
    assert is_allowed("  us-east-1  ")


def test_a_region_outside_the_list_is_rejected():
    # Real AWS regions, deliberately not on the allowed list.
    for region in ["us-gov-west-1", "cn-north-1", "eu-west-4"]:
        assert not is_allowed(region), region


def test_a_group_code_is_rejected():
    # 'use1' is a group code, not a region ID.
    assert not is_allowed("use1")
    assert not is_allowed("usw2")


def test_junk_is_rejected():
    for value in ["", "   ", "not-a-region", "us-east", "US East (N. Virginia)", None]:
        assert not is_allowed(value), value


def test_normalize_region_lowercases_and_trims():
    assert normalize_region("  US-EAST-1 ") == "us-east-1"
    assert normalize_region("us-east-1") == "us-east-1"
    assert normalize_region(None) == ""
    assert normalize_region("") == ""

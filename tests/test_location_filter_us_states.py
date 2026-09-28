"""Test is_target_location — respects config geography.

With a Canada/Québec-focused config.json, Canadian and remote locations
must be accepted while unrelated US on-site and foreign locations are rejected.
"""
import pytest
from scrape_jobs import is_target_location, _config_targets_non_us, TARGET_LOCATIONS


def test_config_targets_canada():
    assert _config_targets_non_us() is True
    assert any("canada" in t or "quebec" in t or "québec" in t for t in TARGET_LOCATIONS)


@pytest.mark.parametrize("location", [
    "Montreal, Quebec, Canada",
    "Quebec, Canada",
    "Toronto, Ontario, Canada",
    "Remote, Canada",
    "Remote",
    "Hybrid - Montreal, QC",
    "Télétravail, Québec",
    "Laval, QC",
    "Greater Montreal",
])
def test_canada_locations_accepted(location):
    assert is_target_location(location) is True, f'"{location}" should be accepted'


@pytest.mark.parametrize("location", [
    "Sacramento, California, United States",
    "Austin, Texas, United States",
    "New York, New York, United States",
    "London, United Kingdom",
    "Berlin, Germany",
    "Tokyo, Japan",
    "Mumbai, India",
])
def test_non_target_locations_rejected(location):
    assert is_target_location(location) is False, f'"{location}" should be rejected'


def test_remote_united_states_kept_for_manual_review():
    # "remote" is an explicit location_filter term — keep for dashboard triage
    # (scoring_profile penalizes US-only eligibility language).
    assert is_target_location("Remote, United States") is True


def test_empty():
    assert is_target_location("") is False


def test_none():
    assert is_target_location(None) is False  # type: ignore[arg-type]

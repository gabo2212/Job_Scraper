"""Geo eligibility / seniority / scam screens for remote-board postings (no network)."""
import pytest

from remote_geo import (
    assess_seniority,
    classify_location,
    description_geo_restriction,
    looks_scammy,
    required_years,
    title_is_non_target,
)


@pytest.mark.parametrize("loc,status,scope", [
    ("Worldwide", "eligible", "worldwide"),
    ("Anywhere in the World", "eligible", "worldwide"),
    ("Global", "eligible", "worldwide"),
    ("Canada", "eligible", "canada"),
    ("Remote - Canada", "eligible", "canada"),
    ("Canada,  USA", "eligible", "canada-friendly"),
    ("USA, Canada, Argentina, Mexico, Peru", "eligible", "canada-friendly"),
    ("Europe, LATAM, APAC, the U.S., Canada", "eligible", "canada-friendly"),
    ("North America", "eligible", "north-america"),
    ("Northern America, LATAM, Europe, APAC", "eligible", "north-america"),
    ("Americas, Europe, Israel", "eligible", "north-america"),
    ("EMEA + Americas", "eligible", "north-america"),
    ("Remote - Americas", "eligible", "north-america"),
    ("US/Canada", "eligible", "canada-friendly"),
    ("EST timezone", "eligible", "north-america"),
    ("", "unclear", "unclear"),
    ("Remote", "unclear", "unclear"),
    ("Fully remote", "unclear", "unclear"),
    ("Redwood City", "unclear", "unclear"),
    ("USA", "ineligible", "us-only"),
    ("United States", "ineligible", "us-only"),
    ("Remote - US", "ineligible", "us-only"),
    ("Remote, USA Only", "ineligible", "us-only"),
    ("US only", "ineligible", "us-only"),
    ("Austin, Texas", "ineligible", "us-only"),
    ("Boston, MA", "ineligible", "us-only"),
    ("Anywhere in the US", "ineligible", "us-only"),
    ("Europe", "ineligible", "eu-uk-only"),
    ("EMEA", "ineligible", "eu-uk-only"),
    ("UK", "ineligible", "eu-uk-only"),
    ("Germany", "ineligible", "eu-uk-only"),
    ("Time zone: CET (+/- 3 hours)", "ineligible", "eu-uk-only"),
    ("EMEA or APAC time zones only", "ineligible", "other-region"),
    ("APAC", "ineligible", "apac-only"),
    ("Singapore", "ineligible", "apac-only"),
    ("Philippines, Guatemala, Nicaragua, South Africa", "ineligible", "apac-only"),
    ("LATAM", "ineligible", "latam-only"),
    ("Brazil", "ineligible", "latam-only"),
    ("Internationally located (not in the US, CA, UK, NZ, or AU)", "ineligible", "other-region"),
])
def test_classify_location(loc, status, scope):
    assert classify_location(loc) == (status, scope)


def test_timezone_only_positive_offsets_is_ineligible():
    assert classify_location("", timezones=[1, 2, 3])[0] == "ineligible"
    # Americas-compatible offsets stay unclear (judged by triage)
    assert classify_location("", timezones=[-8, -5, 1])[0] == "unclear"


@pytest.mark.parametrize("text,expected", [
    ("You must reside in the United States to apply.", "us-only"),
    ("This role is open to candidates residing in the US.", "us-only"),
    ("Applicants must be legally authorized to work in the United States.", "us-only"),
    ("This is a US-remote position.", "us-only"),
    ("Samsara: This role is remote and open to candidates residing in the US.", "us-only"),
    ("You must be located in the UK or EU for legal reasons.", "eu-uk-only"),
    ("We are EU-only for now.", "eu-uk-only"),
    ("You need the right to work in the UK.", "eu-uk-only"),
    ("Fully remote anywhere. We love juniors.", ""),
    ("Open to Canada or the United States; must reside in the US or Canada.", ""),
    ("", ""),
])
def test_description_geo_restriction(text, expected):
    assert description_geo_restriction(text) == expected


def test_explicit_canada_location_beats_jd_boilerplate():
    jd = "Our HQ team members must reside in the United States (see benefits page)."
    assert description_geo_restriction(jd, location_scope="canada") == ""
    assert description_geo_restriction(jd, location_scope="worldwide") == "us-only"


@pytest.mark.parametrize("text,years", [
    ("3+ years of experience with Python", 3),
    ("Minimum 5 years building APIs", 5),
    ("at least 4 yrs", 4),
    ("experience of 6 years in backend", 6),
    ("1-2 years experience preferred", 1),
    ("2-3 years of experience", 2),
    ("0-2 years experience", 0),
    ("Our company has 15 years of experience in the market.", 0),
    ("Founded 10 years ago, we are growing", 0),
    ("No prior experience required", 0),
    ("", 0),
])
def test_required_years(text, years):
    assert required_years(text) == years


@pytest.mark.parametrize("title,desc,hint,expected", [
    ("Junior Python Developer", "We need 0-2 years.", "", "junior"),
    ("Software Engineer", "5+ years of professional experience required.", "", "senior"),
    ("Software Engineer", "We welcome graduates. Entry-level welcome.", "", "junior"),
    ("Software Engineer", "Build APIs with Python and Postgres.", "", "neutral"),
    ("Software Engineer", "We are seeking a senior-level engineer.", "", "senior"),
    ("Software Engineer", "Join us.", "Senior", "senior"),
    ("Software Engineer", "Join us.", "Mid-level", "senior"),
    ("Software Engineer", "Join us.", "Entry-level", "junior"),
    ("Software Engineer", "Join us.", "Any", "neutral"),
    ("Software Engineer", "Join us.", "Entry-Level, Junior", "junior"),
    ("Junior Developer", "2-3 years of experience", "", "junior"),
    ("Web Developer (Fresh graduate welcome)", "on-job-training", "", "junior"),
])
def test_assess_seniority(title, desc, hint, expected):
    assert assess_seniority(title, desc, level_hint=hint) == expected


@pytest.mark.parametrize("title,expected", [
    ("Data Scientist II, Infrastructure", "seniority"),
    ("Software Engineer III", "seniority"),
    ("Werkstudent AI Agents & Social Growth (m/w/d)", "student/co-op"),
    ("AI Automation Engineer Co-op", "student/co-op"),
    ("Online Tutor Extra Income - Voice Recordings - AI trainer", "scam/low-quality"),
    ("Native Chinese Speaker Jobs - AI trainer", "scam/low-quality"),
    ("Junior Python Developer", ""),
    ("Software Engineer I, Frontend", ""),
    ("QA Automation Tester", ""),
])
def test_title_is_non_target(title, expected):
    assert title_is_non_target(title) == expected


def test_scam_detection():
    assert looks_scammy("Data Entry", "Acme", "Contact us on WhatsApp to start earning")
    assert looks_scammy("Assistant", "Acme", "You will pay a training fee of $99")
    assert looks_scammy("Developer", "Confidential", "Build things")
    assert not looks_scammy("Junior Developer", "Acme Labs", "Build APIs with Python. Mentorship included.")

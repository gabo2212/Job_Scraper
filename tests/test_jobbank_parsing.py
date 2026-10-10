"""Unit tests for Job Bank Canada HTML card parsing (no network)."""
from pathlib import Path

from scrape_jobs import (
    _jobbank_parse_date,
    _jobbank_posting_url,
    _jobbank_telework_fields,
    _parse_jobbank_articles,
    title_matches_keywords,
)

FIXTURE = Path(__file__).parent / "fixtures" / "jobbank_search_results.html"


def test_parse_fixture_articles():
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = _parse_jobbank_articles(html)
    # 2 real sample cards + 3 synthetic (remote junior, FR hybrid, senior on-site)
    assert len(jobs) >= 5
    for job in jobs:
        assert job["ats"] == "JobBank"
        assert job["title"]
        assert job["url"].startswith("https://www.jobbank.gc.ca/jobsearch/jobposting/")
        assert ";jsessionid=" not in job["url"]


def test_remote_and_hybrid_and_french_titles():
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = {j["title"].lower(): j for j in _parse_jobbank_articles(html)}

    remote = jobs["junior it support technician"]
    assert remote["is_remote"] is True
    assert remote["telework"].lower() == "remote"
    assert "Remote" in (remote["work_arrangement"] or "")
    assert remote["company"] == "Acme Cloud Support Inc."
    assert "Montreal" in remote["location"]
    assert "$28.00" in remote["salary"]
    assert remote["date_posted"] == "2026-09-20"

    fr = next(j for j in jobs.values() if "technicien en informatique" in j["title"].lower())
    assert fr["telework"].lower() == "hybrid"
    assert fr["work_arrangement"] == "Hybrid"
    assert "Québec" in fr["location"] or "Quebec" in fr["location"]
    assert "55,000" in fr["salary"]


def test_jsessionid_stripped_from_url():
    url = _jobbank_posting_url(
        "/jobsearch/jobposting/99900002;jsessionid=ABC123.jobsearch1?source=searchresults"
    )
    assert url == "https://www.jobbank.gc.ca/jobsearch/jobposting/99900002"


def test_date_parsing_en_and_fr():
    assert _jobbank_parse_date("September 24, 2026") == "2026-09-24"
    assert _jobbank_parse_date("24 septembre 2026") == "2026-09-24"


def test_telework_badge_mapping():
    assert _jobbank_telework_fields("Remote")[0] is True
    assert _jobbank_telework_fields("Hybrid")[1] == "Hybrid"
    assert _jobbank_telework_fields("On site")[0] is False


def test_empty_html():
    assert _parse_jobbank_articles("") == []
    assert _parse_jobbank_articles("<html><body>no jobs</body></html>") == []


def test_keyword_filter_keeps_junior_drops_senior():
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = _parse_jobbank_articles(html)
    kept = [j for j in jobs if title_matches_keywords(j["title"])]
    # IT support / help desk titles are in scope; senior architect titles drop.
    assert any("support" in j["title"].lower() for j in kept)
    assert title_matches_keywords("Junior Python Developer")
    assert title_matches_keywords("junior IT support technician")
    assert not title_matches_keywords("senior IT architect")

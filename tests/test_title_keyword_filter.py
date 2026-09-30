"""Junior independent-path keyword filter: soft nouns + hard support queues."""
from scrape_jobs import title_matches_keywords, _title_is_excluded, _requires_too_many_years


def test_keeps_junior_independent_titles():
    keep = [
        "Junior Python Developer — Remote",
        "Junior Software Engineer",
        "Junior Full Stack Developer",
        "Junior .NET Developer",
        "Associate Software Developer",
        "Junior QA Automation",
        "Software Tester — Junior",
        "Junior DevOps Engineer",
        "Junior Data Analyst",
        "AI Trainer (Remote)",
        "Data Annotation Specialist Junior",
        "Prompt Engineer — Entry Level",
        "Low-code Developer",
        "RPA Developer Junior",
        "Technical Writer IT",
        "Développeur junior",
        "Programmeur Python junior",
        "Testeur logiciel junior",
        "Analyste données junior",
        "Junior Systems Administrator",
        "Cloud Support Associate",
        "Junior Security Analyst",
        "SOC Analyst L1",
        "AI-assisted Developer",
        "Internal Tools Developer Junior",
        "WordPress Developer Junior",
        "Junior Migration Technician",
        "Remote Device Provisioning Technician",
    ]
    for title in keep:
        assert title_matches_keywords(title), f"should keep: {title}"


def test_drops_helpdesk_and_support_queues():
    drop = [
        "Junior IT Support Technician — Remote",
        "Help Desk Agent L1",
        "Service Desk Technician",
        "IT Support Specialist L1",
        "IT Support Specialist",
        "Technical Support Specialist",
        "Desktop Support Technician",
        "Technicien soutien informatique",
        "Agent de soutien technique",
        "Support Engineer L1",
        "IT Support Consultant",
        "Technical Support Representative",
        "Customer Support Agent",
        "Call Center Tech Support",
        "Soutien technique niveau 1",
        "Centre de services analyste",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop support: {title}"


def test_drops_specialist_consultant_without_junior():
    drop = [
        "IT Operations Specialist",
        "Deployment Specialist",
        "Migration Specialist",
        "Data Migration Specialist/Consultant",
        "Implementation Specialist",
        "Deployment Consultant",
        "IT Specialist – Endpoint Management",
        "Coordonnateur TI",
        "IT Coordinator",
        "Security Specialist",
        "SharePoint Specialist",
        "Consultant",
        "Implementation Analyst",
        "Endpoint Management Specialist",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_junior_signal_allows_soft_nouns():
    assert title_matches_keywords("Junior Implementation Consultant")
    assert title_matches_keywords("Junior Deployment Specialist")
    assert title_matches_keywords("Data Annotation Specialist Junior")
    assert title_matches_keywords("Associate Cloud Engineer")
    assert title_matches_keywords("Systems Administrator I")


def test_drops_administrator_engineer_without_junior():
    drop = [
        "Systems Administrator",
        "DevOps Engineer",
        "Cloud Engineer",
        "Security Engineer",
        "Solutions Architect",
        "Ingénieur DevOps",
        "Administrateur réseau senior",
        "Senior Software Engineer",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_drops_hard_excludes():
    drop = [
        "supervisor, technical support",
        "Project Manager, Service Desk",
        "Senior Help Desk Technician",
        "Lead Engineer, Desktop Support",
        "Staff Engineer — IT Support",
        "Business Analyst — IT Support",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_variety_keep_ai_and_dev():
    keep = [
        "Python Developer",
        "Full Stack Developer",
        "Test Automation Engineer",
        "RPA Developer",
        "Data Analyst",
        "ETL Developer",
        "AI Trainer",
        "Prompt Engineer",
        "Low-code Developer",
        "Technical Writer",
        "GitHub Copilot Engineer",
        "AI Agents Developer",
    ]
    for title in keep:
        assert title_matches_keywords(title), f"should keep variety: {title}"


def test_hard_tier_suffix_not_soft_bypassed():
    assert _title_is_excluded("Technical Implementation Specialist II")
    assert not title_matches_keywords("Technical Implementation Specialist II")
    assert not title_matches_keywords("Housekeeping Associate")
    assert not title_matches_keywords("Retail Associate")


def test_years_required_helper():
    assert _requires_too_many_years("Minimum 5 years experience")
    assert _requires_too_many_years("3+ years required")
    assert not _requires_too_many_years("0-2 years experience")
    assert not _requires_too_many_years("1 year preferred")


def test_build_title_re_empty_never_matches():
    from scrape_jobs import _build_title_re

    rx = _build_title_re([])
    assert rx.search("Junior Cloud Engineer") is None
    assert list(rx.finditer("anything")) == []

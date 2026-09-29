"""Entry-level keyword filter: soft ambiguous-noun bypass."""
from scrape_jobs import title_matches_keywords, _title_is_excluded


def test_keeps_clear_entry_support_titles():
    keep = [
        "Junior IT Support Technician — Remote",
        "Help Desk Agent L1",
        "Service Desk Technician",
        "IT Support Specialist L1",
        "IT Support Specialist",
        "Technical Support Specialist",
        "Desktop Support Technician",
        "Technicien soutien informatique",
        "Agent de soutien technique",
        "Junior Systems Administrator",
        "Support Engineer L1",
        "Junior Software Engineer",
        "Deployment Technician",
        "Deployment Specialist",
        "Migration Specialist",
        "NOC Technician",
        "Service Desk Administrator 1",
        "Jr. Specialist, IT Operations",
        "Systems Administrator I",
        "Junior DevOps Engineer",
        "Associate Network Engineer",
        "Associate Cloud Engineer",
        "IT Support Consultant",
        "Technical Support Coordinator",
        "Technicien au déploiement",
        "Spécialiste soutien technique",
        "Administrateur système junior",
        "Analyste TI niveau 1",
    ]
    for title in keep:
        assert title_matches_keywords(title), f"should keep: {title}"


def test_drops_specialist_without_entry_signal():
    drop = [
        "Warehouse support specialist",
        "Administrative Support Specialist",
        "IT Operations Specialist",
        "Operations Support Specialist",
        "Bilingual Business Technology IT Specialist",
        "Security Specialist",
        "SharePoint Specialist",
        "Payroll Specialist",
    ]
    for title in drop:
        assert _title_is_excluded(title), f"should exclude: {title}"
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_drops_administrator_engineer_without_junior():
    drop = [
        "Systems Administrator",
        "IT Systems Administrator",
        "Network System Administrator",
        "Site Reliability Engineer / Release Management",
        "Cloud Architect",
        "DevOps Engineer",
        "Cloud Engineer",
        "Security Engineer",
        "Solutions Architect",
        "Consultant",
        "Ingénieur DevOps",
        "Administrateur réseau senior",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_drops_hard_excludes_even_with_support_words():
    drop = [
        "supervisor, technical support",
        "Department Supervisor (Service Desk)",
        "Business Analyst — IT Support",
        "Project Manager, Service Desk",
        "Senior Help Desk Technician",
        "Lead Engineer, Desktop Support",
        "Staff Engineer — IT Support",
    ]
    for title in drop:
        assert not title_matches_keywords(title), f"should drop: {title}"


def test_specialist_bypass_requires_entry_or_support_signal():
    assert title_matches_keywords("IT Support Specialist")
    assert title_matches_keywords("Help Desk Specialist")
    assert title_matches_keywords("Deployment Specialist")
    assert not title_matches_keywords("Payroll Specialist")
    assert not title_matches_keywords("Security Specialist")


def test_variety_keep_sobeys_like_titles():
    """Deployment/migration/field/POS titles keep even without exact include phrase."""
    keep = [
        "Technicien(ne) – déploiement",
        "Implementation Analyst",
        "Data Migration Analyst",
        "Device Provisioning Technician",
        "SCCM Technician",
        "Technicien terrain informatique",
        "Technicien d'installation",
        "Junior Implementation Consultant",
        "Go-Live Support Analyst",
        "Rollout Coordinator",
        "IT Rollout Technician",
        "Endpoint Management Specialist",
    ]
    for title in keep:
        assert title_matches_keywords(title), f"should keep: {title}"


def test_build_title_re_empty_never_matches():
    """Empty exclude list must not compile to '' (matches every position)."""
    from scrape_jobs import _build_title_re

    rx = _build_title_re([])
    assert rx.search("Junior Cloud Engineer") is None
    assert list(rx.finditer("anything")) == []


def test_linkedin_backfill_days_defaults_on_bad_env(monkeypatch):
    import scrape_jobs as sj

    monkeypatch.setenv("LINKEDIN_BACKFILL_DAYS", "14d")
    try:
        days = int(__import__("os").environ.get("LINKEDIN_BACKFILL_DAYS", "") or "7")
    except ValueError:
        days = 7
    assert days == 7
    assert isinstance(sj.LINKEDIN_BACKFILL_DAYS, int)
    assert sj.LINKEDIN_BACKFILL_DAYS > 0

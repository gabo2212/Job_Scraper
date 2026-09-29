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

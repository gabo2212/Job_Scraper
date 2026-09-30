"""Static guards for the dashboard's "Best shot" view filter (no JS runner in repo)."""
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "triage.html").read_text(encoding="utf-8")


def test_best_shot_toggle_present_and_named():
    assert 'id="best-shot"' in HTML
    assert ">Best shot<" in HTML
    assert 'id="best-shot-ct"' in HTML
    assert 'id="best-shot-note"' in HTML


def test_best_shot_only_strong_or_maybe_and_composes():
    assert "j._verdict === 'strong' || j._verdict === 'maybe'" in HTML
    assert "filters.bestShot && !isBestShot(j)" in HTML
    assert "matchesFilters(j, 'bestshot')" in HTML  # count honors all other filters


def test_best_shot_default_on_and_persisted():
    assert "getItem(BEST_SHOT_KEY) !== '0'" in HTML
    assert "setItem(BEST_SHOT_KEY" in HTML
    # const must be declared before the filters object that reads it (TDZ)
    assert HTML.index("const BEST_SHOT_KEY") < HTML.index("bestShot: readBestShotPref()")


def test_client_prune_keeps_junior_engineer_and_technician_titles():
    line = next(l for l in HTML.splitlines() if "const EXCLUDED_TITLE_RE" in l)
    assert "engineer" not in line and "technician" not in line

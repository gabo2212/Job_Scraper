"""Static guards for the tab/device sync wiring in triage.html + tracker-sync.js."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "triage.html").read_text(encoding="utf-8")
JS = (ROOT / "tracker-sync.js").read_text(encoding="utf-8")


def test_sync_module_loaded_before_dashboard_script():
    assert HTML.index('<script src="tracker-sync.js"></script>') < HTML.index("// ---------- Sources ----------")


def test_writes_are_diffs_onto_latest_not_whole_state_overwrites():
    assert "applyViewChanges(latest, baselineView, cur, now)" in HTML
    assert "BroadcastChannel('jobTriage:tracker')" in HTML
    assert "ev.key === TRACKER_KEY" in HTML          # storage-event fallback
    assert "visibilitychange" in HTML


def test_prune_does_not_delete_tracking_data():
    m = re.search(r"function pruneState\(\) \{.*?\n  \}\n", HTML, re.S)
    assert m, "pruneState not found"
    body = m.group(0)
    assert "delete state.notes" not in body
    assert "delete state.stars" not in body
    assert "delete state.timeline" not in body


def test_sync_is_off_by_default_and_token_stays_local():
    assert "let syncCfg = null;" in HTML
    assert "localStorage.setItem(SYNC_CFG_KEY" in HTML
    # the token must never be logged or placed in a URL
    sync_block = HTML[HTML.index("Optional device sync via a secret GitHub Gist"):]
    assert not re.search(r"console\.(log|info|warn|error|debug)\([^)]*(token|syncCfg)", sync_block)
    assert "?token" not in HTML and "access_token" not in HTML


def test_setup_fragment_is_scrubbed_from_the_address_bar():
    i = HTML.index("function importSetupFragment")
    assert "history.replaceState(null, '', location.pathname + location.search)" in HTML[i:i + 700]


def test_only_github_gist_api_is_contacted():
    hosts = set(re.findall(r"https?://[a-z0-9.\-]+", JS))
    # github.com appears only in user-facing copy; the single API host is api.github.com
    assert "https://api.github.com" in hosts
    assert not (hosts - {"https://api.github.com"})
    for ep in re.findall(r"call\('(?:GET|POST|PATCH|DELETE)', '([^']+)'", JS):
        assert ep.startswith("/gists")


def test_no_hardcoded_tokens():
    for text in (HTML, JS):
        assert not re.search(r"gh[pousr]_[A-Za-z0-9]{30,}", text)
        assert "github_pat_" not in text


def test_gist_is_created_secret():
    assert "public: false" in JS


def test_readme_documents_sync():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Sync across tabs and devices" in readme
    assert "scopes=gist" in readme

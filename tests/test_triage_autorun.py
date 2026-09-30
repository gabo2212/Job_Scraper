"""Auto-triage plumbing: the unscored gate and the triage.yml trigger design."""
import importlib.util
import json
from pathlib import Path

import pytest

import triage_agent

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


# --------------------------------------------------------------------------
# find_unscored
# --------------------------------------------------------------------------

def _job(url, date="2026-09-01"):
    return {"url": url, "title": "T", "date_posted": date}


def test_find_unscored_skips_scored_and_urlless():
    jobs = [_job("a"), _job("b"), {"title": "no url"}]
    scores = {"a": {"verdict": "strong", "score": 9}}
    assert [j["url"] for j in triage_agent.find_unscored(jobs, scores)] == ["b"]


def test_find_unscored_retries_error_verdicts():
    jobs = [_job("a"), _job("b")]
    scores = {"a": {"verdict": "error"}, "b": {"verdict": "skip"}}
    assert [j["url"] for j in triage_agent.find_unscored(jobs, scores)] == ["a"]


def test_find_unscored_freshest_first_and_does_not_mutate_input():
    jobs = [_job("old", "2026-08-01"), _job("new", "2026-09-20"), _job("none", "")]
    snapshot = list(jobs)
    out = triage_agent.find_unscored(jobs, {})
    assert [j["url"] for j in out] == ["new", "old", "none"]
    assert jobs == snapshot


def test_find_unscored_empty():
    assert triage_agent.find_unscored([], {}) == []


# --------------------------------------------------------------------------
# scripts/count_unscored.py
# --------------------------------------------------------------------------

def _load_counter():
    spec = importlib.util.spec_from_file_location(
        "count_unscored", REPO / "scripts" / "count_unscored.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def triage_paths(tmp_path, monkeypatch):
    jobs_path = tmp_path / "all_jobs.json"
    scores_path = tmp_path / "scores.json"
    monkeypatch.setattr(triage_agent, "ALL_JOBS_PATH", str(jobs_path))
    monkeypatch.setattr(triage_agent, "SCORES_PATH", str(scores_path))
    return jobs_path, scores_path


def test_count_zero_when_no_master_file(triage_paths):
    assert _load_counter().count_unscored() == 0


def test_count_unscored_and_github_output(triage_paths, tmp_path, monkeypatch, capsys):
    jobs_path, scores_path = triage_paths
    jobs_path.write_text(json.dumps({"jobs": [
        _job("a"), _job("b"), _job("c")]}), encoding="utf-8")
    scores_path.write_text(json.dumps({"scores": {
        "a": {"verdict": "strong"}, "b": {"verdict": "error"}}}), encoding="utf-8")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    assert _load_counter().main() == 0
    assert out.read_text(encoding="utf-8").strip() == "unscored=2"
    assert "unscored roles: 2" in capsys.readouterr().out


def test_count_zero_writes_zero_output(triage_paths, tmp_path, monkeypatch):
    jobs_path, scores_path = triage_paths
    jobs_path.write_text(json.dumps({"jobs": [_job("a")]}), encoding="utf-8")
    scores_path.write_text(json.dumps({"scores": {"a": {"verdict": "maybe"}}}),
                           encoding="utf-8")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _load_counter().main()
    assert out.read_text(encoding="utf-8").strip() == "unscored=0"


def test_count_handles_non_ascii_master(triage_paths):
    jobs_path, _ = triage_paths
    jobs_path.write_text(json.dumps({"jobs": [
        {"url": "u", "title": "Développeur junior", "company": "Société"}]},
        ensure_ascii=False), encoding="utf-8")
    assert _load_counter().count_unscored() == 1


# --------------------------------------------------------------------------
# triage.yml trigger design
# --------------------------------------------------------------------------

yaml = pytest.importorskip("yaml")


def _workflow(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _all_workflow_names():
    return {_workflow(p.name)["name"] for p in WORKFLOWS.glob("*.yml")}


def test_triage_has_all_three_triggers():
    on = _workflow("triage.yml")["on"]
    assert "schedule" in on
    assert "workflow_dispatch" in on
    assert on["workflow_run"]["types"] == ["completed"]


def test_workflow_run_targets_exist_and_exclude_self():
    wf = _workflow("triage.yml")
    targets = wf["on"]["workflow_run"]["workflows"]
    assert wf["name"] not in targets, "triage must never trigger itself"
    assert set(targets) <= _all_workflow_names(), "unknown workflow name in trigger"
    assert "Remote Boards Watcher" in targets


def test_triage_not_triggered_by_non_data_workflows():
    targets = set(_workflow("triage.yml")["on"]["workflow_run"]["workflows"])
    for noisy in ("Tests", "Clear Job Data", "Sync from upstream",
                  "Triage Agent Evals"):
        assert noisy not in targets


def test_score_job_only_reacts_to_successful_runs():
    cond = _workflow("triage.yml")["jobs"]["score"]["if"]
    assert "workflow_run.conclusion == 'success'" in cond
    assert "github.workflow" in cond  # self-trigger guard


def test_model_call_gated_on_unscored_count():
    steps = _workflow("triage.yml")["jobs"]["score"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    assert by_name["Count unscored roles"]["id"] == "count"
    for name in ("Install model SDKs", "Run triage agent", "Stage scores for commit"):
        assert by_name[name]["if"] == "steps.count.outputs.unscored != '0'"


def test_score_job_outside_commit_group_and_serialized():
    score = _workflow("triage.yml")["jobs"]["score"]
    assert score["concurrency"]["group"] == "triage-score"
    assert score["concurrency"]["cancel-in-progress"] is False
    assert score["permissions"] == {"contents": "read"}


def test_commit_job_uses_shared_group_and_flag():
    commit = _workflow("triage.yml")["jobs"]["commit"]
    assert commit["concurrency"]["group"] == "job-scraper-commit-push"
    assert commit["concurrency"]["cancel-in-progress"] is False
    assert "vars.ENABLE_DATA_COMMITS == 'true'" in commit["if"]
    assert commit["permissions"] == {"contents": "write"}


def test_commit_job_only_writes_scores_file():
    """Loop guard: the auto-triage must never rewrite the scrape outputs."""
    text = (WORKFLOWS / "triage.yml").read_text(encoding="utf-8")
    adds = [l.strip() for l in text.splitlines() if "git add" in l]
    assert adds == ["git add -f output/scores.json"]


def test_limits_bounded_for_both_paths():
    step = next(s for s in _workflow("triage.yml")["jobs"]["score"]["steps"]
                if s.get("name") == "Run triage agent")
    limit = step["env"]["TRIAGE_LIMIT"]
    assert "'100'" in limit and "'300'" in limit
    assert '--limit "$TRIAGE_LIMIT"' in step["run"]


def test_remote_boards_watcher_contract():
    wf = _workflow("remote_boards_watch.yml")
    assert "schedule" in wf["on"] and "workflow_dispatch" in wf["on"]
    assert wf["concurrency"]["group"] == "job-scraper-commit-push"
    text = (WORKFLOWS / "remote_boards_watch.yml").read_text(encoding="utf-8")
    assert "vars.ENABLE_DATA_COMMITS == 'true'" in text
    assert "--remoteboards-only" in text

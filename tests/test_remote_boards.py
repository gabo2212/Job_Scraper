"""Remote-board parsers, screening pipeline and scrape wiring (saved fixtures, mocked network)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import remote_boards as rb
import scrape_jobs as sj

FX = Path(__file__).parent / "fixtures" / "remote_boards"
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _text(name):
    return (FX / name).read_text(encoding="utf-8")


def _json(name):
    return json.loads(_text(name))


# --------------------------------------------------------------------------- helpers

def test_html_to_text_keeps_paragraphs_and_trims():
    txt = rb.html_to_text("<p>Hello&nbsp;<b>world</b></p><ul><li>One</li><li>Two</li></ul><script>x()</script>")
    assert txt == "Hello world\nOne\nTwo"
    assert rb.html_to_text("<p>" + "a" * 5000 + "</p>") == "a" * rb.MAX_DESC_CHARS
    assert rb.html_to_text("plain text & more") == "plain text & more"
    assert rb.html_to_text("") == ""


@pytest.mark.parametrize("value,expected", [
    ("2026-09-21T12:55:11", "2026-09-21"),
    (1790779973, "2026-09-30"),
    (1790779973000, "2026-09-30"),
    ("Wed, 30 Sep 2026 14:50:43 +0000", "2026-09-30"),
    ("", ""),
    (None, ""),
    ("garbage", ""),
])
def test_parse_date(value, expected):
    assert rb.parse_date(value) == expected


def test_is_stale_respects_window():
    assert rb.is_stale("2026-07-01", max_age_days=45, now=NOW)
    assert not rb.is_stale("2026-09-20", max_age_days=45, now=NOW)
    assert not rb.is_stale("", max_age_days=45, now=NOW)


# --------------------------------------------------------------------------- parsers

def test_parse_remotive():
    rows = rb.parse_remotive(_json("remotive.json"))
    jr = next(r for r in rows if r["title"] == "Junior Python Developer")
    assert jr["ats"] == "Remotive" and jr["company"] == "Acme Labs"
    assert jr["location"] == "Worldwide" and jr["date_posted"] == "2026-09-28"
    assert jr["is_remote"] is True and "0-2 years" in jr["description"]
    assert jr["source_site"] == "https://remotive.com"
    assert all("<" not in r["description"] for r in rows)


def test_parse_remoteok_skips_legal_notice_and_uses_link_back_url():
    rows = rb.parse_remoteok(_json("remoteok.json"))
    assert rows and all(r["ats"] == "RemoteOK" for r in rows)
    assert all(r["url"].startswith("https://remoteok.com/") for r in rows)  # link back, not apply_url
    assert not any("API Terms" in r["title"] for r in rows)


def test_parse_wwr_splits_company_and_unescapes():
    rows = rb.parse_wwr(_text("wwr.xml"))
    by_title = {r["title"]: r for r in rows}
    reddit = by_title["Backend Engineer, IAM"]
    assert reddit["company"] == "Reddit"
    assert reddit["ats"] == "WeWorkRemotely" and reddit["location"] == "Anywhere in the World"
    assert reddit["url"].startswith("https://weworkremotely.com/remote-jobs/")
    assert "<" not in reddit["description"]
    assert reddit["date_posted"] and reddit["_expires"]


def test_parse_himalayas_handles_string_and_object_locations():
    rows = {r["title"]: r for r in rb.parse_himalayas(_json("himalayas.json"))}
    jr = rows["Junior Full-Stack Developer"]
    assert jr["location"] == "Canada" and jr["_level_hint"] == "Entry-level"
    assert jr["salary"].startswith("55,000-70,000 CAD")
    assert jr["date_posted"] == "2026-09-30"
    obj = rows["Junior Data Analyst"]
    assert obj["location"] == "Canada"  # {"alpha2","name","slug"} object form
    assert rows["Backend Developer"]["location"] == "Worldwide"  # empty = worldwide


def test_parse_jobicy_workingnomads_arbeitnow_hn():
    jobicy = rb.parse_jobicy(_json("jobicy.json"))
    assert any(r["location"] == "Canada" and r["ats"] == "Jobicy" for r in jobicy)
    wn = rb.parse_workingnomads(_json("workingnomads.json"))
    assert all(r["ats"] == "WorkingNomads" for r in wn) and len(wn) == 4
    arb = rb.parse_arbeitnow(_json("arbeitnow.json"))
    assert all(r["ats"] == "Arbeitnow" for r in arb)
    assert any(r["location"] == "Worldwide" for r in arb)  # no ", Germany" appended
    hn = rb.parse_hn_thread(_json("hn_thread.json"))
    fit = next(r for r in hn if r["company"] == "FitMate")
    assert fit["url"].startswith("https://news.ycombinator.com/item?id=")
    assert "REMOTE" in fit["location"]


# --------------------------------------------------------------------------- screening

def _all_fixture_jobs():
    jobs = []
    jobs += rb.parse_remotive(_json("remotive.json"))
    jobs += rb.parse_remoteok(_json("remoteok.json"))
    jobs += rb.parse_wwr(_text("wwr.xml"))
    jobs += rb.parse_himalayas(_json("himalayas.json"))
    jobs += rb.parse_jobicy(_json("jobicy.json"))
    jobs += rb.parse_workingnomads(_json("workingnomads.json"))
    jobs += rb.parse_arbeitnow(_json("arbeitnow.json"))
    jobs += rb.parse_hn_thread(_json("hn_thread.json"))
    return jobs


@pytest.fixture(scope="module")
def screened():
    stats = rb.ScreenStats()
    kept = rb.screen_jobs(_all_fixture_jobs(), title_ok=sj.remote_board_title_ok,
                          max_age_days=10000, now=NOW, stats=stats)
    return kept, stats


def _kept(screened, ats, title):
    return next((j for j in screened[0] if j["ats"] == ats and j["title"] == title), None)


def test_screen_keeps_junior_remote_canada_eligible(screened):
    assert _kept(screened, "Himalayas", "Junior Full-Stack Developer")["geo_scope"] == "canada"
    assert _kept(screened, "Remotive", "Junior Python Developer")["geo_scope"] == "worldwide"
    assert _kept(screened, "WeWorkRemotely",
                 "Web Developer (Fresh graduate welcome, WFH policy, on-job-training)")["seniority_signal"] == "junior"
    assert _kept(screened, "HNHiring", "ML Engineer")["geo_scope"] == "worldwide"
    assert _kept(screened, "Arbeitnow", "Junior Python Developer")["geo_scope"] == "worldwide"


def test_screen_unclear_location_kept_with_geo_unclear_tag(screened):
    job = _kept(screened, "RemoteOK", "Software Engineer")
    assert job["work_arrangement"] == rb.GEO_UNCLEAR_LABEL
    assert job["geo_scope"] == "unclear"
    assert "geo-unclear" in job["location"].lower()


def test_screen_drops_us_only_eu_apac_latam(screened):
    kept_titles = {(j["ats"], j["title"]) for j in screened[0]}
    assert ("RemoteOK", "Junior QA Tester") not in kept_titles            # Remote - US
    assert ("WeWorkRemotely", "SaaS Full-Stack Developer (100 % remote) (m/f/d)") not in kept_titles  # Europe
    assert ("RemoteOK", "Frontend Engineer") not in kept_titles           # Singapore/Ireland
    assert ("WeWorkRemotely", "Backend Engineer, IAM") not in kept_titles  # JD: US residents only
    assert ("Himalayas", "AI Engineer") not in kept_titles                # United States
    assert ("Himalayas", "Junior QA Automation Engineer") not in kept_titles  # tz offsets all east of UTC
    reasons = screened[1].dropped
    assert any(k.startswith("geo:") for k in reasons["RemoteOK"])
    assert "geo:jd-us-only" in reasons["WeWorkRemotely"]
    assert "geo:latam-only" in reasons["WorkingNomads"]


def test_screen_drops_senior_support_spam_and_expired(screened):
    titles = {j["title"] for j in screened[0]}
    assert "Senior Shopify Developer" not in titles
    assert "Backend Developer" not in titles  # Himalayas level=Senior
    assert not any("Extra Income" in t for t in titles)  # mass-posted gig spam
    assert "Junior Data Analyst" not in titles  # expiryDate in the past
    assert "expired" in screened[1].dropped["Himalayas"]
    assert "scam/low-quality" in screened[1].dropped["Himalayas"]
    assert "seniority" in screened[1].dropped["Himalayas"]


def test_screen_counts_raw_vs_kept_and_reports(screened):
    stats = screened[1]
    assert stats.raw["Himalayas"] == 6
    assert stats.kept["Himalayas"] == 1
    report = "\n".join(stats.report())
    assert "Himalayas" in report and "raw=6" in report and "kept=1" in report


def test_screen_drops_stale_and_malformed():
    stale = rb.parse_remotive(_json("remotive.json"))
    later = datetime(2027, 6, 1, tzinfo=timezone.utc)
    stats = rb.ScreenStats()
    kept = rb.screen_jobs(stale, title_ok=sj.remote_board_title_ok, max_age_days=10, now=later, stats=stats)
    assert kept == []
    assert stats.dropped["Remotive"]["stale"] >= 1
    bad = [{"ats": "Remotive", "title": "", "url": "", "company": "x"}]
    stats = rb.ScreenStats()
    assert rb.screen_jobs(bad, title_ok=lambda t: True, stats=stats) == []
    assert stats.dropped["Remotive"]["malformed"] == 1


def test_screen_does_not_mutate_input():
    rows = rb.parse_remotive(_json("remotive.json"))
    snapshot = json.dumps(rows, sort_keys=True)
    rb.screen_jobs(rows, title_ok=sj.remote_board_title_ok, max_age_days=10000, now=NOW)
    assert json.dumps(rows, sort_keys=True) == snapshot or all(
        "geo_scope" not in r for r in rows)


def test_kept_jobs_carry_trimmed_description_and_attribution(screened):
    for job in screened[0]:
        assert len(job["description"]) <= rb.MAX_DESC_CHARS
        assert job["source_site"].startswith("http")
        assert job["is_remote"] is True
        assert not any(k.startswith("_") for k in job)


# --------------------------------------------------------------------------- title filter reuse

@pytest.mark.parametrize("title,ok", [
    ("Junior Python Developer", True),
    ("Software Engineer", True),
    ("Backend Developer", True),
    ("Frontend Web Application Developer", True),
    ("QA Tester", True),
    ("AI Trainer", True),
    ("Senior Software Engineer", False),
    ("Staff Backend Engineer", False),
    ("Engineering Manager", False),
    ("Customer Support Engineer", False),
    ("Technical Support Specialist", True),
    ("Help Desk Analyst L1", True),
    ("Sales Engineer", False),
    ("Solutions Consultant", False),
    ("Mechanical Engineer", False),
    ("Content Marketing Manager", False),
])
def test_remote_board_title_filter_matches_other_sources_rules(title, ok):
    assert sj.remote_board_title_ok(title) is ok


def test_force_soft_bypass_never_helps_specialist_or_hard_excludes():
    assert sj._title_is_excluded("Security Specialist", force_soft_bypass=True)
    assert not sj._title_is_excluded("Help Desk Developer", force_soft_bypass=True)
    assert not sj._title_is_excluded("Backend Developer", force_soft_bypass=True)
    # default behaviour unchanged
    assert sj._title_is_excluded("Application Developer")


# --------------------------------------------------------------------------- shared helpers extended

def test_work_arrangement_geo_unclear_label_is_preserved():
    assert sj.classify_work_arrangement("Remote (geo-unclear)") == "Remote (geo-unclear)"
    job = sj._ensure_work_arrangement({"work_arrangement": "Remote (geo-unclear)", "location": "Remote - ", "title": "x"})
    assert job["work_arrangement"] == "Remote (geo-unclear)"
    assert sj.classify_work_arrangement("Remote") == "Remote"


def test_is_target_location_accepts_worldwide_not_us_only_or_eu():
    assert sj.is_target_location("Worldwide")
    assert sj.is_target_location("Anywhere in the World")
    assert sj.is_target_location("Americas")
    assert not sj.is_target_location("Germany")
    assert not sj.is_target_location("Singapore")


# --------------------------------------------------------------------------- fetchers (mocked get)

def _router(routes, calls):
    def get(url):
        calls.append(url)
        for prefix, payload in routes.items():
            if url.startswith(prefix):
                return payload() if callable(payload) else payload
        return ""
    return get


def test_fetch_himalayas_one_page_per_query_with_polite_sleep():
    calls, sleeps = [], []
    get = _router({"https://himalayas.app/jobs/api/search": _text("himalayas.json")}, calls)
    rows = rb.fetch_himalayas(get, {"queries": ["python", "qa"], "max_pages": 3, "delay": 2.0},
                              sleep=sleeps.append)
    assert len(calls) == 2  # fewer than 20 rows per page -> never requests page 2
    assert "q=python" in calls[0] and "seniority=Entry-level" in calls[0] and "country=CA" in calls[0]
    assert sleeps == [2.0]
    assert len({r["url"] for r in rows}) == len(rows)  # deduped across queries


def test_fetch_jobicy_and_wwr_one_request_per_endpoint():
    calls, sleeps = [], []
    get = _router({"https://jobicy.com/api/v2/remote-jobs": _text("jobicy.json"),
                   "https://weworkremotely.com/": _text("wwr.xml")}, calls)
    jobicy = rb.fetch_jobicy(get, {"geos": ["canada", "anywhere"], "count": 100, "delay": 1.5}, sleep=sleeps.append)
    assert [c for c in calls if "jobicy" in c] == [
        "https://jobicy.com/api/v2/remote-jobs?count=100&geo=canada",
        "https://jobicy.com/api/v2/remote-jobs?count=100&geo=anywhere"]
    assert sleeps == [1.5] and jobicy
    calls.clear()
    wwr = rb.fetch_wwr(get, {"feeds": ["remote-programming-jobs", "remote-jobs"], "delay": 0}, sleep=lambda s: None)
    assert calls == ["https://weworkremotely.com/categories/remote-programming-jobs.rss",
                     "https://weworkremotely.com/remote-jobs.rss"]
    assert len({r["url"] for r in wwr}) == len(wwr)


def test_fetch_hn_picks_latest_thread_and_ignores_stale_ones():
    hits = {"hits": [
        {"title": "Ask HN: Freelancer? Seeking freelancer? (September 2026)", "objectID": "1", "created_at": "2026-09-01T15:00:00Z"},
        {"title": "Ask HN: Who is hiring? (September 2026)", "objectID": "42", "created_at": "2026-09-01T15:01:17Z"},
    ]}
    calls = []
    get = _router({"https://hn.algolia.com/api/v1/search_by_date": json.dumps(hits),
                   "https://hn.algolia.com/api/v1/items/42": _text("hn_thread.json")}, calls)
    rows = rb.fetch_hn_hiring(get, {"delay": 0}, sleep=lambda s: None, now=NOW)
    assert rows and calls[-1].endswith("/items/42") and len(calls) == 2
    # a thread older than 40 days is not fetched at all
    calls.clear()
    old = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert rb.fetch_hn_hiring(get, {"delay": 0}, sleep=lambda s: None, now=old) == []
    assert len(calls) == 1


def test_fetch_boards_isolates_failures_and_reports_them():
    calls = []

    def get(url):
        calls.append(url)
        if "remoteok" in url:
            raise RuntimeError("boom")
        if "remotive" in url:
            return _text("remotive.json")
        return ""  # blocked / empty

    rows, errors = rb.fetch_boards(["remotive", "remoteok", "workingnomads", "nope"], {}, get=get, sleep=lambda s: None)
    assert rows and all(r["ats"] == "Remotive" for r in rows)
    assert "RuntimeError" in errors["remoteok"]
    assert "no rows" in errors["workingnomads"]
    assert errors["nope"] == "unknown board"


def test_http_get_never_raises_and_does_not_retry_429(monkeypatch):
    from urllib.error import HTTPError
    attempts = []

    def fake_urlopen(req, timeout=0):
        attempts.append(req.full_url)
        raise HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(rb, "urlopen", fake_urlopen)
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    assert rb.http_get("https://example.com/api") == ""
    assert len(attempts) == 1  # 429 is never retried
    assert "JobScraperPersonal" in rb.USER_AGENT and "github.com/gabo2212" in rb.USER_AGENT


# --------------------------------------------------------------------------- end-to-end scrape

def _all_routes(calls):
    return _router({
        "https://remotive.com/api/remote-jobs": _text("remotive.json"),
        "https://remoteok.com/api": _text("remoteok.json"),
        "https://weworkremotely.com/": _text("wwr.xml"),
        "https://himalayas.app/jobs/api/search": _text("himalayas.json"),
        "https://jobicy.com/api/v2/remote-jobs": _text("jobicy.json"),
        "https://www.workingnomads.com/api/exposed_jobs/": _text("workingnomads.json"),
        "https://www.arbeitnow.com/api/job-board-api": _text("arbeitnow.json"),
        "https://hn.algolia.com/api/v1/search_by_date": json.dumps({"hits": []}),
    }, calls)


def test_scrape_and_save_writes_outputs_and_merges_into_all_jobs(tmp_output_dir, monkeypatch):
    monkeypatch.setattr(sj, "REMOTE_BOARDS_MAX_AGE_DAYS", 100000)
    calls = []
    jobs = sj.scrape_remoteboards_recent(get=_all_routes(calls), sleep=lambda s: None)
    assert jobs and all(j["ats"] in rb.BOARD_SITES for j in jobs)
    sj.save_remoteboards_results(jobs)
    out = json.loads((tmp_output_dir / "remoteboards_jobs.json").read_text(encoding="utf-8"))
    assert out["total"] == len(jobs) == out["new_count"]
    assert (tmp_output_dir / "remoteboards_jobs.md").exists()
    assert (tmp_output_dir / "remoteboards_jobs.html").exists()
    master = json.loads((tmp_output_dir / "all_jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert {j["url"] for j in jobs} <= {j["url"] for j in master}
    unclear = [j for j in master if j.get("geo_scope") == "unclear"]
    assert unclear and all(j["work_arrangement"] == "Remote (geo-unclear)" for j in unclear)
    assert all(j.get("description") for j in master)
    # polite: exactly one request to single-endpoint boards
    assert sum(u.startswith("https://remotive.com/api/remote-jobs") for u in calls) == 1
    assert sum(u.startswith("https://remoteok.com/api") for u in calls) == 1
    assert sum(u.startswith("https://www.workingnomads.com/api/exposed_jobs/") for u in calls) == 1


def test_scrape_preserves_previous_rows_when_everything_is_blocked(tmp_output_dir, monkeypatch):
    monkeypatch.setattr(sj, "REMOTE_BOARDS_MAX_AGE_DAYS", 100000)
    sj.save_remoteboards_results(sj.scrape_remoteboards_recent(get=_all_routes([]), sleep=lambda s: None))
    before = json.loads((tmp_output_dir / "remoteboards_jobs.json").read_text(encoding="utf-8"))["total"]
    assert before > 0
    blocked = sj.scrape_remoteboards_recent(get=lambda url: "", sleep=lambda s: None)
    assert len(blocked) == before


def test_scrape_carries_over_rows_from_a_board_that_failed(tmp_output_dir, monkeypatch):
    monkeypatch.setattr(sj, "REMOTE_BOARDS_MAX_AGE_DAYS", 100000)
    sj.save_remoteboards_results(sj.scrape_remoteboards_recent(get=_all_routes([]), sleep=lambda s: None))
    first = json.loads((tmp_output_dir / "remoteboards_jobs.json").read_text(encoding="utf-8"))["jobs"]
    himalayas_before = [j for j in first if j["ats"] == "Himalayas"]
    assert himalayas_before

    def get(url):  # Himalayas down, everything else fine
        return "" if "himalayas" in url else _all_routes([])(url)

    second = sj.scrape_remoteboards_recent(get=get, sleep=lambda s: None)
    assert {j["url"] for j in himalayas_before} <= {j["url"] for j in second}


def test_remote_boards_config_wires_search_terms_into_himalayas_queries():
    cfg = sj._remote_boards_config()
    assert cfg["himalayas"].get("queries")
    assert "remotive" in [b.lower() for b in cfg.get("boards", rb.DEFAULT_BOARDS)]


# --------------------------------------------------------------------------- triage JD text

def test_triage_fetch_jd_uses_inline_description_without_network(monkeypatch):
    import triage_agent

    def boom(*a, **k):
        raise AssertionError("must not fetch pages for jobs that carry a description")

    monkeypatch.setattr(triage_agent, "_http_get", boom)
    job = {"ats": "Himalayas", "url": "https://himalayas.app/x", "description": "  Junior role.\n" + "z" * 9000}
    jd = triage_agent.fetch_jd(job)
    assert jd.startswith("Junior role.") and len(jd) <= triage_agent.JD_MAX_CHARS
    # sources without a stored description still return '' (metadata-only)
    assert triage_agent.fetch_jd({"ats": "JobBank", "url": "https://www.jobbank.gc.ca/x"}) == ""


# --------------------------------------------------------------------------- company career boards (Greenhouse / Lever / Ashby)

def _company_rows():
    return (rb.parse_greenhouse_board(_json("greenhouse_board.json"), "Acme GH")
            + rb.parse_lever_board(_json("lever_board.json"), "Acme Lever")
            + rb.parse_ashby_board(_json("ashby_board.json"), "Acme Ashby"))


def test_parse_greenhouse_unescapes_content_and_flags_remote_by_location():
    rows = rb.parse_greenhouse_board(_json("greenhouse_board.json"), "Acme GH")
    by_id = {r["url"].rsplit("/", 1)[1]: r for r in rows}
    ok = by_id["1001"]
    assert ok["ats"] == "CompanyBoards" and ok["company"] == "Acme GH"
    assert ok["date_posted"] == "2026-09-20" and "Greenhouse" in ok["tags"] and "Engineering" in ok["tags"]
    assert "<" not in ok["description"] and "AI-assisted coding tools" in ok["description"]
    assert ok["_not_remote"] is False and by_id["1002"]["_not_remote"] is True


def test_parse_lever_uses_workplace_type_country_and_lists():
    rows = {r["url"].rsplit("/", 1)[1]: r for r in rb.parse_lever_board(_json("lever_board.json"), "Acme Lever")}
    assert rows["a1"]["_not_remote"] is False and rows["a2"]["_not_remote"] is True
    assert rows["a1"]["location"] == "Montreal, Canada"
    assert "Cursor and Claude" in rows["a1"]["description"] and "0-2 years experience" in rows["a1"]["description"]
    assert rows["a1"]["job_type"] == "Full-time" and rows["a1"]["date_posted"].startswith("2026-")


def test_parse_ashby_prefers_workplace_type_and_skips_unlisted():
    rows = rb.parse_ashby_board(_json("ashby_board.json"), "Acme Ashby")
    assert [r["url"].rsplit("/", 1)[1] for r in rows] == ["x1", "x2"]  # x3 is unlisted
    assert rows[0]["_not_remote"] is False and rows[1]["_not_remote"] is True  # isRemote ignored when workplaceType set
    assert rows[0]["salary"] == "CAD 70K - 85K" and rows[0]["location"] == "Canada"


def test_company_board_screening_requires_remote_and_explicit_junior_signal():
    stats = rb.ScreenStats()
    kept = rb.screen_jobs(_company_rows(), title_ok=sj.remote_board_title_ok, now=NOW, stats=stats)
    kept_ids = {j["url"].rsplit("/", 1)[1] for j in kept}
    assert kept_ids == {"1001", "a1", "x1"}  # remote + junior + Canada-eligible
    drops = stats.dropped["CompanyBoards"]
    assert drops["not-remote"] == 3          # 1002, a2, x2
    assert drops["stale"] == 1               # 1006 is >120 days old
    assert drops["seniority"] >= 1           # 1003: 5+ years
    assert drops["no-junior-signal"] >= 1    # 1004: bare Software Engineer, no junior cue
    assert any(k.startswith("geo:") for k in drops)  # 1005: Remote - US
    assert all(j["ats"] == "CompanyBoards" and j["work_arrangement"] in ("Remote", "Remote (geo-unclear)") for j in kept)
    assert all(not k.startswith("_") for j in kept for k in j)


def test_company_boards_use_longer_freshness_window_than_other_boards():
    row = _company_rows()[0]
    row["date_posted"] = "2026-07-15"  # 77 days old: stale for a 45d board, fresh for company boards
    assert rb.screen_jobs([dict(row)], title_ok=lambda t: True, max_age_days=45, now=NOW)
    other = dict(row, ats="Remotive")
    other.pop("_max_age_days")
    assert rb.screen_jobs([other], title_ok=lambda t: True, max_age_days=45, now=NOW) == []


def test_fetch_companyboards_one_request_per_company_with_delay_and_validation():
    calls, sleeps = [], []
    get = _router({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": _text("greenhouse_board.json"),
        "https://api.lever.co/v0/postings/acme?mode=json": _text("lever_board.json"),
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true": _text("ashby_board.json"),
    }, calls)
    cfg = {"company_delay": 0.5, "companies": [
        {"name": "Acme GH", "ats": "greenhouse", "slug": "acme"},
        {"name": "Acme Lever", "ats": "lever", "slug": "acme"},
        {"name": "Acme Ashby", "ats": "ashby", "slug": "acme"},
        {"name": "Gone", "ats": "greenhouse", "slug": "does-not-exist"},
        {"name": "Evil", "ats": "greenhouse", "slug": "../../etc/passwd"},
        {"name": "Unknown ATS", "ats": "workable", "slug": "acme"},
    ]}
    rows = rb.fetch_companyboards(get, cfg, sleep=sleeps.append)
    assert len(calls) == 4                       # 3 hits + 1 missing board; invalid entries make no request
    assert sleeps == [0.5, 0.5, 0.5]             # a pause before every request except the first
    assert {r["company"] for r in rows} == {"Acme GH", "Acme Lever", "Acme Ashby"}
    assert all(c.startswith("https://") for c in calls)


def test_fetch_boards_wires_companyboards_with_config_subsection():
    calls = []
    get = _router({"https://boards-api.greenhouse.io/": _text("greenhouse_board.json")}, calls)
    rows, errors = rb.fetch_boards(["companyboards"], {"companyboards": {"companies": [
        {"name": "Acme", "ats": "greenhouse", "slug": "acme"}]}}, get=get, sleep=lambda s: None)
    assert rows and not errors and "CompanyBoards" in rb.BOARD_SITES
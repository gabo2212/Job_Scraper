"""Remote-first job boards: fetch + normalize + geo/seniority screening.

Pure helpers used by ``scrape_jobs.py --remoteboards-only``. Nothing here reads
config or touches the filesystem: every network call goes through an injectable
``get`` callable so tests run against saved fixtures with no network.

Boards (all free / public, no login, no captcha):
  Remotive      JSON API   remotive.com/api/remote-jobs  (<=4 req/day, link back)
  RemoteOK      JSON API   remoteok.com/api              (link back + credit)
  WeWorkRemotely RSS       weworkremotely.com/...rss     (public feeds)
  Himalayas     JSON API   himalayas.app/jobs/api/search (1 req/day data, link back)
  Jobicy        JSON API   jobicy.com/api/v2/remote-jobs (credit + original URL)
  WorkingNomads JSON       workingnomads.com/api/exposed_jobs/
  Arbeitnow     JSON API   arbeitnow.com/api/job-board-api (Germany-centric)
  HNHiring      Algolia    "Ask HN: Who is hiring?" monthly thread

Every job record carries ``ats`` (board label), a plain-text ``description``
(trimmed) and ``geo_scope`` so the triage agent / dashboard can judge it.
"""
from __future__ import annotations

import html as html_mod
import inspect
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = (
    "JobScraperPersonal/1.0 (+https://github.com/gabo2212/Job_Scraper; "
    "personal non-commercial job search; polite: 1 run/day)"
)
MAX_DESC_CHARS = 3000
DEFAULT_MAX_AGE_DAYS = 45
DEFAULT_DELAY = 2.0

GEO_UNCLEAR_LABEL = "Remote (geo-unclear)"

# Board label -> homepage (attribution / link-back requirement).
BOARD_SITES = {
    "Remotive": "https://remotive.com",
    "RemoteOK": "https://remoteok.com",
    "WeWorkRemotely": "https://weworkremotely.com",
    "Himalayas": "https://himalayas.app",
    "Jobicy": "https://jobicy.com",
    "WorkingNomads": "https://www.workingnomads.com",
    "Arbeitnow": "https://www.arbeitnow.com",
    "HNHiring": "https://news.ycombinator.com",
}

from remote_geo import (  # noqa: F401  (re-exported for callers/tests)
    _JUNIOR_SIGNAL_RE, assess_seniority, classify_location, description_geo_restriction,
    looks_scammy, title_is_non_target,
)

GetFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# HTTP + text helpers
# ---------------------------------------------------------------------------

def http_get(url: str, *, timeout: float = 25.0) -> str:
    """GET with an honest User-Agent. Returns '' on any failure (never raises).

    429/403 are NOT retried - we never hammer a board that pushed back.
    One retry only for transient 5xx/network errors.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    for attempt in range(2):
        try:
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            if e.code in (429, 403, 401, 404):
                print(f"  WARNING: {url} -> HTTP {e.code}; not retrying")
                return ""
            if attempt == 0:
                time.sleep(8.0)
                continue
            print(f"  WARNING: {url} -> HTTP {e.code}")
            return ""
        except (URLError, TimeoutError, OSError) as e:
            if attempt == 0:
                time.sleep(8.0)
                continue
            print(f"  WARNING: {url} -> {e}")
            return ""
    return ""


class _Text(HTMLParser):
    BLOCK = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol"}
    SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str, *, limit: int = MAX_DESC_CHARS) -> str:
    """HTML (or already-plain text) -> trimmed plain text with paragraph breaks."""
    if not markup:
        return ""
    if "<" in markup and ">" in markup:
        p = _Text()
        try:
            p.feed(markup)
            p.close()
            text = "".join(p.parts)
        except Exception:  # malformed markup: fall back to tag stripping
            text = re.sub(r"<[^>]+>", " ", markup)
    else:
        text = markup
    text = html_mod.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()  # compact: one line per block
    return text[:limit].rstrip()


def parse_date(value) -> str:
    """ISO date (YYYY-MM-DD) from ISO strings / epoch secs|ms / RFC-822. '' if unknown."""
    if value in (None, "", 0):
        return ""
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r"\d{9,13}", str(value).strip()):
            n = float(value)
            if n > 1e11:  # milliseconds
                n /= 1000.0
            return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def is_stale(date_iso: str, *, max_age_days: int, now: datetime | None = None) -> bool:
    if not date_iso:
        return False
    try:
        posted = datetime.strptime(date_iso[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return posted < now - timedelta(days=max_age_days)


# ---------------------------------------------------------------------------
# Normalizers (raw board payload -> common record)
# ---------------------------------------------------------------------------

def _fmt_money(lo, hi, cur: str = "USD", period: str = "") -> str:
    def _n(v):
        try:
            return f"{int(float(v)):,}"
        except (TypeError, ValueError):
            return ""
    lo_s, hi_s = _n(lo), _n(hi)
    if not lo_s and not hi_s:
        return ""
    rng = lo_s if not hi_s or lo_s == hi_s else f"{lo_s}-{hi_s}" if lo_s else hi_s
    return f"{rng} {cur or 'USD'}" + (f"/{period}" if period and period != "annual" else "/yr")


def _record(ats: str, *, title, company, url, location, date_posted, description_html,
            salary="", tags=None, job_type="", level_hint="", timezones=None) -> dict:
    return {
        "company": html_mod.unescape(str(company or "").strip()),
        "title": html_mod.unescape(re.sub(r"\s+", " ", str(title or "")).strip()),
        "location": html_mod.unescape(str(location or "").strip()),
        "url": str(url or "").strip(),
        "date_posted": date_posted or "",
        "salary": salary or "",
        "ats": ats,
        "is_remote": True,
        "telework": "Remote",
        "job_type": job_type or "",
        "description": html_to_text(description_html),
        "tags": [str(t) for t in (tags or []) if t][:12],
        "source_site": BOARD_SITES.get(ats, ""),
        "_level_hint": level_hint or "",
        "_timezones": list(timezones or []),
    }


def parse_remotive(payload: dict) -> list[dict]:
    out = []
    for j in (payload or {}).get("jobs", []) or []:
        out.append(_record(
            "Remotive", title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
            location=j.get("candidate_required_location"),
            date_posted=parse_date(j.get("publication_date")),
            description_html=j.get("description"), salary=str(j.get("salary") or ""),
            tags=[j.get("category"), *(j.get("tags") or [])], job_type=str(j.get("job_type") or "").replace("_", "-")))
    return out


def parse_remoteok(payload: list) -> list[dict]:
    out = []
    for j in payload or []:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is the legal notice
        slug_url = j.get("url") or (f"https://remoteok.com/remote-jobs/{j.get('slug')}" if j.get("slug") else "")
        slug_url = re.sub(r"^(https?://)remoteok\.com", r"\1remoteok.com", slug_url, flags=re.I)  # 'remoteOK.com'
        out.append(_record(
            "RemoteOK", title=_fix_mojibake(j.get("position")), company=j.get("company"),
            url=slug_url, location=j.get("location"),
            date_posted=parse_date(j.get("date") or j.get("epoch")),
            description_html=j.get("description"),
            salary=_fmt_money(j.get("salary_min"), j.get("salary_max")) if (j.get("salary_min") or 0) > 0 else "",
            tags=j.get("tags")))
    return out


def _fix_mojibake(s) -> str:
    s = str(s or "")
    if "Ã" in s or "â€" in s:
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def parse_wwr(xml_text: str) -> list[dict]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml_text or "", re.S):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
            if not m:
                return ""
            v = m.group(1).strip()
            cdata = re.match(r"<!\[CDATA\[(.*?)\]\]>$", v, re.S)
            # CDATA already holds literal HTML; plain text nodes are entity-escaped.
            return cdata.group(1) if cdata else html_mod.unescape(v)
        raw_title = tag("title")
        company, _, role = raw_title.partition(": ")
        if not role:
            company, role = "", raw_title
        region = tag("region")
        country = re.sub(r"^[^\w]+", "", tag("country"))
        out.append(_record(
            "WeWorkRemotely", title=role, company=company, url=tag("link") or tag("guid"),
            location=region, date_posted=parse_date(tag("pubDate")),
            description_html=tag("description"),
            tags=[tag("category"), *[s.strip() for s in tag("skills").split(",")]],
            job_type=tag("type")))
        out[-1]["_hq_country"] = country
        out[-1]["_expires"] = parse_date(tag("expires_at"))
    return out


def parse_himalayas(payload: dict) -> list[dict]:
    out = []
    for j in (payload or {}).get("jobs", []) or []:
        locs = []
        for loc in j.get("locationRestrictions") or []:
            locs.append(loc.get("name") if isinstance(loc, dict) else str(loc))
        location = ", ".join(x for x in locs if x) or "Worldwide"
        level = ", ".join(j.get("seniority") or [])
        out.append(_record(
            "Himalayas", title=j.get("title"), company=j.get("companyName"),
            url=j.get("applicationLink") or j.get("guid"), location=location,
            date_posted=parse_date(j.get("pubDate")),
            description_html=j.get("description") or j.get("excerpt"),
            salary=_fmt_money(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "USD", j.get("salaryPeriod") or ""),
            tags=(j.get("categories") or [])[:8], job_type=str(j.get("employmentType") or ""),
            level_hint=level, timezones=j.get("timezoneRestrictions")))
        out[-1]["_expires"] = parse_date(j.get("expiryDate"))
    return out


def parse_jobicy(payload: dict) -> list[dict]:
    out = []
    for j in (payload or {}).get("jobs", []) or []:
        geo = re.sub(r"\s*,\s*", ", ", str(j.get("jobGeo") or "").strip())
        out.append(_record(
            "Jobicy", title=j.get("jobTitle"), company=j.get("companyName"), url=j.get("url"),
            location=geo, date_posted=parse_date(j.get("pubDate")),
            description_html=j.get("jobDescription") or j.get("jobExcerpt"),
            salary=_fmt_money(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency") or "USD",
                              j.get("salaryPeriod") or ""),
            tags=[*(j.get("jobIndustry") or [])], job_type=", ".join(j.get("jobType") or []),
            level_hint=str(j.get("jobLevel") or "")))
    return out


def parse_workingnomads(payload: list) -> list[dict]:
    out = []
    for j in payload or []:
        if not isinstance(j, dict):
            continue
        tags = j.get("tags")
        tags = [t.strip() for t in tags.split(",")] if isinstance(tags, str) else (tags or [])
        out.append(_record(
            "WorkingNomads", title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
            location=j.get("location"), date_posted=parse_date(j.get("pub_date")),
            description_html=j.get("description"), tags=[j.get("category_name"), *tags]))
    return out


def parse_arbeitnow(payload: dict) -> list[dict]:
    out = []
    for j in (payload or {}).get("data", []) or []:
        if not j.get("remote"):
            continue
        loc = str(j.get("location") or "").strip()
        out.append(_record(
            "Arbeitnow", title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
            # Arbeitnow is a German board: a bare city means Germany/EU, not "anywhere".
            location=(f"{loc}, Germany" if loc and not re.search(
                r"germany|worldwide|anywhere|global", loc, re.I) else loc),
            date_posted=parse_date(j.get("created_at")), description_html=j.get("description"),
            tags=j.get("tags"), job_type=", ".join(j.get("job_types") or [])))
    return out


def _hn_first_line(text_html: str) -> str:
    first = re.split(r"<p>|<br\s*/?>|\n", text_html or "", maxsplit=1)[0]
    return html_to_text(first, limit=300).replace("\n", " ").strip()


_HN_REMOTE_RE = re.compile(r"\b(?:remote|wfh|work from home|distributed)\b", re.I)


def parse_hn_thread(payload: dict) -> list[dict]:
    """Top-level comments of an 'Ask HN: Who is hiring?' thread."""
    out = []
    for c in (payload or {}).get("children", []) or []:
        body = c.get("text") or ""
        if not body or c.get("author") is None:
            continue
        first = _hn_first_line(body)
        parts = [p.strip() for p in first.split("|") if p.strip()]
        if len(parts) < 2 or not _HN_REMOTE_RE.search(body[:600]):
            continue
        company = re.sub(r"\s*\(?https?://\S+\)?", "", parts[0]).strip() or parts[0]
        role_parts = [p for p in parts[1:] if not _HN_REMOTE_RE.fullmatch(p) and len(p) <= 120]
        rec = _record(
            "HNHiring", title=", ".join(role_parts[:2])[:140] or parts[1][:140], company=company,
            url=f"https://news.ycombinator.com/item?id={c.get('id')}",
            location=next((p for p in parts[1:] if _HN_REMOTE_RE.search(p)), " | ".join(parts[1:3])),
            date_posted=parse_date(c.get("created_at")),
            description_html=body)
        rec["_hn_parts"] = parts
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Fetchers (one polite request per endpoint; ``get`` is injectable)
# ---------------------------------------------------------------------------

def _json(text: str):
    try:
        return json.loads(text) if text else None
    except (json.JSONDecodeError, ValueError):
        return None


def fetch_remotive(get: GetFn, cfg: dict) -> list[dict]:
    return parse_remotive(_json(get("https://remotive.com/api/remote-jobs")) or {})


def fetch_remoteok(get: GetFn, cfg: dict) -> list[dict]:
    return parse_remoteok(_json(get("https://remoteok.com/api")) or [])


def fetch_wwr(get: GetFn, cfg: dict, *, sleep=time.sleep) -> list[dict]:
    feeds = cfg.get("feeds") or [
        "remote-programming-jobs", "remote-devops-sysadmin-jobs", "remote-jobs"]
    seen: dict[str, dict] = {}
    for i, feed in enumerate(feeds):
        if i:
            sleep(cfg.get("delay", DEFAULT_DELAY))
        path = feed if "/" in feed else (
            f"categories/{feed}" if feed != "remote-jobs" else feed)
        for rec in parse_wwr(get(f"https://weworkremotely.com/{path}.rss")):
            seen.setdefault(rec["url"], rec)
    return list(seen.values())


def fetch_himalayas(get: GetFn, cfg: dict, *, sleep=time.sleep) -> list[dict]:
    """Search endpoint: entry-level + Canada-eligible (country=CA includes worldwide)."""
    queries = cfg.get("queries") or [
        "developer", "python", "qa", "data analyst", "automation", "devops", "ai", "security"]
    pages = max(1, min(int(cfg.get("max_pages", 1) or 1), 3))
    seen: dict[str, dict] = {}
    n = 0
    for q in queries:
        for page in range(1, pages + 1):
            if n:
                sleep(cfg.get("delay", DEFAULT_DELAY))
            n += 1
            params = {"q": q, "country": cfg.get("country", "CA"), "seniority": "Entry-level",
                      "sort": "recent", "page": page}
            payload = _json(get("https://himalayas.app/jobs/api/search?" + urlencode(params))) or {}
            rows = parse_himalayas(payload)
            for rec in rows:
                seen.setdefault(rec["url"], rec)
            if len(payload.get("jobs") or []) < 20:
                break
    return list(seen.values())


def fetch_jobicy(get: GetFn, cfg: dict, *, sleep=time.sleep) -> list[dict]:
    seen: dict[str, dict] = {}
    for i, geo in enumerate(cfg.get("geos") or ["canada", "anywhere"]):
        if i:
            sleep(cfg.get("delay", DEFAULT_DELAY))
        params = {"count": int(cfg.get("count", 100)), "geo": geo}
        payload = _json(get("https://jobicy.com/api/v2/remote-jobs?" + urlencode(params))) or {}
        for rec in parse_jobicy(payload):
            seen.setdefault(rec["url"], rec)
    return list(seen.values())


def fetch_workingnomads(get: GetFn, cfg: dict) -> list[dict]:
    return parse_workingnomads(_json(get("https://www.workingnomads.com/api/exposed_jobs/")) or [])


def fetch_arbeitnow(get: GetFn, cfg: dict) -> list[dict]:
    return parse_arbeitnow(_json(get("https://www.arbeitnow.com/api/job-board-api?page=1")) or {})


def fetch_hn_hiring(get: GetFn, cfg: dict, *, sleep=time.sleep, now: datetime | None = None) -> list[dict]:
    """Latest monthly 'Who is hiring?' thread (only if <= 40 days old)."""
    q = urlencode({"query": "Ask HN: Who is hiring?", "tags": "story,author_whoishiring"})
    hits = (_json(get(f"https://hn.algolia.com/api/v1/search_by_date?{q}")) or {}).get("hits") or []
    now = now or datetime.now(timezone.utc)
    for h in hits:
        if not str(h.get("title", "")).lower().startswith("ask hn: who is hiring"):
            continue
        created = parse_date(h.get("created_at"))
        if is_stale(created, max_age_days=40, now=now):
            return []
        sleep(cfg.get("delay", DEFAULT_DELAY))
        thread = _json(get(f"https://hn.algolia.com/api/v1/items/{h.get('objectID')}")) or {}
        return parse_hn_thread(thread)
    return []


FETCHERS: dict[str, Callable[..., list[dict]]] = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "weworkremotely": fetch_wwr,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "workingnomads": fetch_workingnomads,
    "arbeitnow": fetch_arbeitnow,
    "hnhiring": fetch_hn_hiring,
}
DEFAULT_BOARDS = tuple(FETCHERS)


# ---------------------------------------------------------------------------
# Screening pipeline
# ---------------------------------------------------------------------------

class ScreenStats:
    """Raw vs kept counts per board + a few dropped samples per reason."""

    def __init__(self) -> None:
        self.raw: Counter = Counter()
        self.kept: Counter = Counter()
        self.dropped: dict[str, Counter] = {}
        self.samples: dict[str, list[str]] = {}

    def drop(self, board: str, reason: str, job: dict) -> None:
        self.dropped.setdefault(board, Counter())[reason] += 1
        bucket = self.samples.setdefault(f"{board}:{reason}", [])
        if len(bucket) < 4:
            bucket.append(f"{job.get('title', '')[:70]} @ {job.get('company', '')[:30]} [{job.get('location', '')[:40]}]")

    def report(self) -> list[str]:
        lines = []
        for board in sorted(self.raw):
            d = self.dropped.get(board, Counter())
            why = ", ".join(f"{k}={v}" for k, v in d.most_common()) or "-"
            lines.append(f"  {board:<15} raw={self.raw[board]:<4} kept={self.kept[board]:<3} dropped: {why}")
        return lines


def screen_jobs(jobs: Iterable[dict], *, title_ok: Callable[[str], bool],
                title_has_junior: Callable[[str], bool] | None = None,
                max_age_days: int = DEFAULT_MAX_AGE_DAYS, now: datetime | None = None,
                stats: ScreenStats | None = None) -> list[dict]:
    """Apply title / geo / seniority / scam / freshness screens; return kept jobs.

    ``title_ok`` is scrape_jobs.title_matches_keywords so seniority + help-desk
    exclusion is identical to every other source.
    """
    stats = stats or ScreenStats()
    kept: list[dict] = []
    seen_urls: set[str] = set()
    for job in jobs:
        board = job["ats"]
        stats.raw[board] += 1
        if not job.get("url") or not job.get("title"):
            stats.drop(board, "malformed", job)
            continue
        if job["url"] in seen_urls:
            continue
        seen_urls.add(job["url"])

        if is_stale(job.get("date_posted", ""), max_age_days=max_age_days, now=now):
            stats.drop(board, "stale", job)
            continue

        if board == "HNHiring":
            title = _hn_pick_title(job, title_ok)
            if not title:
                stats.drop(board, "title/no-junior-role", job)
                continue
            job["title"] = title
        elif not title_ok(job["title"]):
            stats.drop(board, "title-filter", job)
            continue

        bad_title = title_is_non_target(job["title"])
        if bad_title:
            stats.drop(board, bad_title, job)
            continue
        if looks_scammy(job["title"], job["company"], job.get("description", "")):
            stats.drop(board, "scam/low-quality", job)
            continue
        if job.get("_expires") and is_stale(job["_expires"], max_age_days=-1, now=now):
            stats.drop(board, "expired", job)
            continue

        if board == "HNHiring":  # geo lives in the header segments after the company name
            status, scope = classify_location(" | ".join(job["_hn_parts"][1:]))
        else:
            status, scope = classify_location(job.get("location", ""), timezones=job.get("_timezones"))
        if status == "ineligible":
            stats.drop(board, f"geo:{scope}", job)
            continue
        jd_scope = description_geo_restriction(job.get("description", ""), location_scope=scope)
        if jd_scope:
            stats.drop(board, f"geo:jd-{jd_scope}", job)
            continue

        level = assess_seniority(job["title"], job.get("description", ""), level_hint=job.get("_level_hint", ""))
        if level == "senior":
            stats.drop(board, "seniority", job)
            continue
        if board == "HNHiring" and level != "junior":
            stats.drop(board, "no-junior-signal", job)
            continue

        kept.append(_finalize(job, status, scope, level))
        stats.kept[board] += 1
    return kept


def _hn_pick_title(job: dict, title_ok: Callable[[str], bool]) -> str:
    """HN posts list several roles; keep the first role segment passing the title filter."""
    if not _JUNIOR_SIGNAL_RE.search(job.get("description", "")):
        return ""
    for part in job.get("_hn_parts", [])[1:]:
        for seg in re.split(r"\s*(?:,|/|;| and )\s*", part):
            seg = seg.strip()
            if 3 <= len(seg) <= 80 and title_ok(seg):
                return seg
    return ""


def _finalize(job: dict, status: str, scope: str, level: str) -> dict:
    rec = {k: v for k, v in job.items() if not k.startswith("_")}
    rec["geo_scope"] = scope
    rec["seniority_signal"] = level
    if status == "unclear":
        rec["work_arrangement"] = GEO_UNCLEAR_LABEL
        rec["location"] = rec.get("location") or GEO_UNCLEAR_LABEL
        if "remote" not in rec["location"].lower():
            rec["location"] = f"Remote - {rec['location']}"
    else:
        rec["work_arrangement"] = "Remote"
        loc = rec.get("location") or "Remote"
        rec["location"] = loc if "remote" in loc.lower() else f"Remote - {loc}"
    return rec


def fetch_boards(boards: Iterable[str], cfg: dict, *, get: GetFn | None = None,
                 sleep=time.sleep) -> tuple[list[dict], dict[str, str]]:
    """Fetch each enabled board once. Returns (raw normalized jobs, {board: error})."""
    get = get or http_get
    all_jobs: list[dict] = []
    errors: dict[str, str] = {}
    delay = float(cfg.get("request_delay", DEFAULT_DELAY) or DEFAULT_DELAY)
    for i, board in enumerate(boards):
        fn = FETCHERS.get(board.lower())
        if fn is None:
            errors[board] = "unknown board"
            continue
        if i:
            sleep(delay)
        sub = dict(cfg.get(board.lower(), {}) or {})
        sub.setdefault("delay", delay)
        try:
            kwargs = {"sleep": sleep} if "sleep" in inspect.signature(fn).parameters else {}
            rows = fn(get, sub, **kwargs)
        except Exception as e:  # one broken board must not sink the others
            errors[board] = f"{type(e).__name__}: {e}"
            rows = []
        if not rows and board not in errors:
            errors[board] = "no rows (empty/blocked)"
        print(f"  {board}: {len(rows)} raw row(s)")
        all_jobs.extend(rows)
    return all_jobs, errors

"""Geo-eligibility, seniority and quality screens for remote-job postings.

Pure functions (no network, no config): decide whether a remote posting is open
to a Canada-based junior candidate, whether the JD asks for too many years, and
whether it looks like scam / mass-posted gig spam. Used by ``remote_boards.py``.
"""
from __future__ import annotations

import html as html_mod
import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Geo eligibility (Canada-friendly?)
# ---------------------------------------------------------------------------

_CANADA_RE = re.compile(
    r"\bcanad(?:a|ian)\b|\bqu[eé]bec\b|\bontario\b|\btoronto\b|\bmontr[eé]al\b|"
    r"\bvancouver\b|\bbritish columbia\b|\balberta\b|\bcanada[- ]friendly\b",
    re.I,
)
_NORTH_AMERICA_RE = re.compile(
    r"\bnorth(?:ern)?[- ]americ(?:a|an)\b|\bnorth america\b|\bamericas\b|\bnam\b|"
    r"\bus\s*/\s*canada\b|\bamerican time ?zones?\b",
    re.I,
)
_WORLDWIDE_RE = re.compile(
    r"\bworld[- ]?wide\b|\banywhere(?!\s+in\s+(?:the\s+)?(?:us|u\.s\.?|usa|united states|"
    r"europe|eu|uk|latam|asia|apac|india|germany|france|spain|brazil)\b)|"
    r"\bglobal(?:ly)?\b|\baround the world\b|\bwork from anywhere\b|\bfully distributed\b|"
    r"\bany ?where in the world\b",
    re.I,
)
# Explicit "not Canada": "not in the US, CA, UK ..." style exclusions.
_EXCLUDES_CANADA_RE = re.compile(
    r"\bnot\s+(?:in|from|based in|located in|available in)\b[^.;]{0,60}\b(?:canada|ca)\b", re.I)

_US_ONLY_LOC_RE = re.compile(
    r"\b(?:us|usa|u\.s\.a?\.?|united states(?: of america)?)\s*[- ]?"
    r"(?:only|based|residents?|citizens?|timezones? only|remote)\b|"
    r"\bonly\s+(?:in\s+)?(?:the\s+)?(?:us|usa|united states)\b|"
    r"\bremote[,\s(\-–]+(?:us|usa|u\.s\.|united states)\b",
    re.I,
)
_US_STATES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    "georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    "massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
    "new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|"
    "oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|"
    "texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming"
)
_US_ABBR = (
    "al|ak|az|ar|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|ma|md|me|mi|mn|mo|ms|mt|nc|nd|"
    "ne|nh|nj|nm|nv|ny|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|va|vt|wa|wi|wv|wy|dc"
)
# Regions / countries that are NOT Canada. Word-bounded; evaluated only when no
# Canada / North-America / worldwide marker is present.
_OTHER_REGION_RE = re.compile(
    r"\b(?:us|usa|u\.s\.a?\.?|united states(?: of america)?|the u\.s\.|"
    r"europe(?:an)?|eu|emea|uk|united kingdom|england|scotland|wales|ireland|"
    r"germany|france|spain|portugal|italy|netherlands|belgium|poland|romania|bulgaria|"
    r"sweden|norway|denmark|finland|switzerland|austria|czech(?:ia)?|hungary|greece|"
    r"serbia|ukraine|turkey|t[uü]rkiye|israel|"
    r"apac|asia(?:[- ]pacific)?|india|pakistan|bangladesh|philippines|vietnam|indonesia|"
    r"malaysia|singapore|japan|china|korea|thailand|australia|new zealand|oceania|"
    r"latam|latin america|south america|brazil|argentina|colombia|chile|peru|mexico|"
    r"central america|africa|nigeria|kenya|south africa|egypt|middle east|uae|"
    r"united arab emirates|saudi arabia|qatar)\b|"
    rf"\b(?:{_US_STATES})\b|"
    rf",\s*(?:{_US_ABBR})\b",
    re.I,
)
_TZ_NA_RE = re.compile(r"\b(?:est|edt|pst|pdt|cst|cdt|mst|mdt|et|pt|ct)\b(?!\w)", re.I)
_TZ_OTHER_RE = re.compile(r"\b(?:cet|cest|eet|eest|gmt\s*[+]|utc\s*[+]\s*[1-9]|ist|jst|aest|sgt|emea|apac)\b", re.I)
_PLAIN_REMOTE = {
    "", "remote", "remote-first", "remote first", "fully remote", "100% remote", "distributed",
    "remoto", "home office", "work from home", "wfh", "telecommute", "remote job", "n/a", "na",
}

# Description-level restrictions (override worldwide/unclear location labels).
_DESC_US_RE = re.compile(
    r"(?:must|need to|required to|have to|should)\s+(?:currently\s+)?(?:be\s+)?"
    r"(?:reside|residing|live|living|located|based|resident)\s+(?:in|within)\s+(?:the\s+)?"
    r"(?:continental\s+|contiguous\s+)?(?:u\.?s\.?a?\.?|united states)\b|"
    r"(?:open|available|restricted|limited|eligible)\s+(?:only\s+)?to\s+"
    r"(?:candidates|applicants|residents|individuals|those|people|persons)?\s*"
    r"(?:who\s+(?:are\s+)?)?(?:residing|living|located|based)?\s*(?:in|within)\s+"
    r"(?:the\s+)?(?:u\.?s\.?a?\.?|united states)\b|"
    r"(?:candidates|applicants|employees|residents)\s+(?:residing|living|located|based)\s+"
    r"(?:in|within)\s+(?:the\s+)?(?:u\.?s\.?a?\.?|united states)\b|"
    r"(?:open to candidates|candidates)\s+(?:residing|living|located|based)\s+in\s+(?:the\s+)?"
    r"(?:us|u\.s\.|united states)|"
    r"(?:u\.?s\.?a?\.?|united states)[- ]?(?:only|remote)\b|\bremote\s*[-–(]\s*(?:us|usa|u\.s\.|united states)\b|"
    r"\b(?:legally\s+)?(?:authorized|eligible|permitted)\s+to\s+work\s+in\s+the\s+"
    r"(?:u\.?s\.?a?\.?|united states)\b|"
    r"\bu\.?s\.?\s+work\s+(?:authorization|permit|eligibility)\b|"
    r"\b(?:u\.?s\.?|united states)\s+citizen(?:s|ship)?\b|"
    r"\bwe(?:'re| are)\s+hiring\s+(?:only\s+)?(?:in|within)\s+the\s+(?:u\.?s\.?a?\.?|united states)\b",
    re.I,
)
_DESC_OTHER_RE = re.compile(
    r"(?:must|need to|required to|have to)\s+(?:be\s+)?(?:located|based|reside|residing|living|resident)\s+"
    r"(?:in|within)\s+(?:the\s+)?(?:eu|e\.u\.|european union|europe|uk|united kingdom|emea|apac|"
    r"latam|latin america|india|australia|germany|brazil|poland|philippines)\b|"
    r"\b(?:eu|uk|europe|emea|apac|latam)[- ]only\b|"
    r"\bright\s+to\s+work\s+in\s+the\s+(?:uk|eu|european union)\b|"
    r"\beu\s+work\s+(?:permit|authorization|authorisation)\b|"
    r"(?:working hours|overlap|time ?zone)[^.\n]{0,40}\b(?:cet|cest|eet|gmt\s*\+\s*\d|utc\s*\+\s*\d|emea)\b",
    re.I,
)


def _match_outside_canada(pattern: re.Pattern, text: str, *, window: int = 90) -> re.Match | None:
    """First match of ``pattern`` not sitting next to a Canada mention."""
    for m in pattern.finditer(text):
        lo, hi = max(0, m.start() - window), min(len(text), m.end() + window)
        if not _CANADA_RE.search(text[lo:hi]):
            return m
    return None


_EU_RE = re.compile(r"\b(?:eu|europe|european|emea|uk|united kingdom|germany|france|spain|poland|romania|"
                    r"portugal|italy|netherlands|ireland|belgium|sweden|norway|switzerland|cet|cest|eet)\b", re.I)
_APAC_RE = re.compile(r"\b(?:apac|asia|india|philippines|japan|china|singapore|australia|new zealand|oceania|"
                      r"vietnam|indonesia|malaysia|korea|thailand|pakistan|bangladesh)\b", re.I)
_LATAM_RE = re.compile(r"\b(?:latam|latin america|south america|brazil|argentina|colombia|chile|peru|"
                       r"mexico|central america)\b", re.I)
_US_TOKEN_RE = re.compile(r"\b(?:us|usa|u\.s\.a?\.?|united states|the u\.s\.)\b|\b(?:" + _US_STATES
                          + r")\b|,\s*(?:" + _US_ABBR + r")\b", re.I)


def _positive_offsets_only(timezones: Iterable | None) -> bool:
    """True when every accepted UTC offset is east of Greenwich (no Americas overlap)."""
    offs = []
    for t in timezones or []:
        try:
            offs.append(float(t))
        except (TypeError, ValueError):
            continue
    return bool(offs) and all(o >= 0 for o in offs) and not any(o <= -3 for o in offs)


def classify_location(location: str, *, timezones: Iterable | None = None) -> tuple[str, str]:
    """Classify a remote-job location label for a Canada-based candidate.

    Returns ``(status, scope)`` where status is ``eligible`` / ``unclear`` /
    ``ineligible`` and scope is a short machine label
    (``canada`` | ``canada-friendly`` | ``north-america`` | ``worldwide`` |
    ``unclear`` | ``us-only`` | ``eu-uk-only`` | ``apac-only`` | ``latam-only`` | ``other-region``).
    Structured timezone restrictions (Himalayas) veto worldwide/unclear labels whose
    accepted offsets are all east of Greenwich.
    """
    status, scope = _classify_text(location)
    if scope in ("worldwide", "unclear") and _positive_offsets_only(timezones):
        return "ineligible", "other-region"
    return status, scope


def _classify_text(location: str) -> tuple[str, str]:
    raw = html_mod.unescape(str(location or "")).strip()
    text = re.sub(r"\s+", " ", raw).lower()
    if text in _PLAIN_REMOTE:
        return "unclear", "unclear"
    if _EXCLUDES_CANADA_RE.search(text):
        return "ineligible", "other-region"
    has_canada = bool(_CANADA_RE.search(text))
    if _US_ONLY_LOC_RE.search(text) and not has_canada:
        return "ineligible", "us-only"
    if has_canada:
        only_canada = not _OTHER_REGION_RE.search(text) and not _NORTH_AMERICA_RE.search(text)
        return "eligible", "canada" if only_canada else "canada-friendly"
    if _NORTH_AMERICA_RE.search(text):
        return "eligible", "north-america"
    if _WORLDWIDE_RE.search(text):
        return "eligible", "worldwide"
    if _TZ_NA_RE.search(text) and not _TZ_OTHER_RE.search(text):
        return "eligible", "north-america"
    if _OTHER_REGION_RE.search(text) or _TZ_OTHER_RE.search(text):
        if _US_TOKEN_RE.search(text):
            return "ineligible", "us-only"
        hits = [name for name, rx in (("eu-uk-only", _EU_RE), ("apac-only", _APAC_RE),
                                      ("latam-only", _LATAM_RE)) if rx.search(text)]
        return "ineligible", hits[0] if len(hits) == 1 else "other-region"
    return "unclear", "unclear"


def description_geo_restriction(description: str, *, location_scope: str = "") -> str:
    """Scope label if the JD text restricts to a region that excludes Canada, else ''.

    An explicit Canada/North-America location label wins over JD boilerplate
    (``location_scope`` in canada / canada-friendly / north-america).
    """
    if not description or location_scope in ("canada", "canada-friendly", "north-america"):
        return ""
    if _match_outside_canada(_DESC_US_RE, description):
        return "us-only"
    if _match_outside_canada(_DESC_OTHER_RE, description):
        return "eu-uk-only"
    return ""


# ---------------------------------------------------------------------------
# Seniority / quality screening from structured level + JD text
# ---------------------------------------------------------------------------

_JUNIOR_SIGNAL_RE = re.compile(
    r"\b(?:junior|jr\.?|entry[- ]?level|new[- ]?grads?|recent graduates?|graduates? welcome|"
    r"early[- ]career|fresh graduates?|beginner|trainee|apprentice|"
    r"no (?:prior |previous |professional )?experience(?: (?:required|necessary|needed))?|"
    r"experience (?:is )?not (?:required|necessary)|0\s*(?:-|–|to)\s*[12]\s*years?|"
    r"1\s*(?:-|–|to)\s*2\s*years?|all (?:experience )?levels|all levels of experience|"
    r"(?:open|welcome)\s+to\s+(?:all\s+)?(?:junior|entry))\b",
    re.I,
)
_SENIOR_DESC_RE = re.compile(
    r"\b(?:senior[- ]level|seeking (?:a |an )?(?:highly )?(?:experienced|senior|seasoned)|"
    r"we(?:'re| are) (?:looking for|hiring|seeking) (?:a |an )?(?:senior|staff|principal|lead)|"
    r"mid[- ]to[- ]senior|mid[- ]level|staff[- ]level|principal[- ]level|expert[- ]level|"
    r"lead (?:a|the) team of|own (?:the )?(?:architecture|technical direction))\b",
    re.I,
)
_YEARS_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_YEARS_EXP_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,70}?\b(?:experience|exp\b|background|track record)|"
    r"\b(?:experience|background)\b[^.\n]{0,50}?\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b|"
    r"\b(?:minimum(?: of)?|at least|min\.?)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
    re.I,
)
_COMPANY_YEARS_CTX_RE = re.compile(
    r"\b(?:our|we(?:'ve| have)?|combined|collective|team|company|founded|over the (?:past|last))\b[^.\n]{0,30}$",
    re.I,
)
SENIOR_LEVELS = {"senior", "sr", "lead", "manager", "director", "executive", "staff", "principal",
                 "mid-level", "midweight", "mid level", "mid", "intermediate"}
JUNIOR_LEVELS = {"entry-level", "entry level", "junior", "entry-level, junior", "intern", "graduate"}


def required_years(text: str) -> int:
    """Largest lower-bound years-of-experience requirement mentioned in ``text`` (0 if none)."""
    if not text:
        return 0
    best = 0
    ranged_spans = []
    for m in _YEARS_RANGE_RE.finditer(text):
        ranged_spans.append(m.span())
        if _COMPANY_YEARS_CTX_RE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        best = max(best, int(m.group(1)))
    for m in _YEARS_EXP_RE.finditer(text):
        if any(s <= m.start() < e for s, e in ranged_spans):
            continue
        if _COMPANY_YEARS_CTX_RE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        n = next((int(g) for g in m.groups() if g), 0)
        best = max(best, n)
    return best


def assess_seniority(title: str, description: str, *, level_hint: str = "") -> str:
    """'junior' | 'neutral' | 'senior' from structured level, title and JD text."""
    hint = (level_hint or "").strip().lower()
    hint_parts = {p.strip() for p in re.split(r"[,/|]", hint) if p.strip()}
    title_junior = bool(_JUNIOR_SIGNAL_RE.search(title or ""))
    years = required_years(description)
    if hint_parts & JUNIOR_LEVELS or title_junior or _JUNIOR_SIGNAL_RE.search(description or ""):
        junior = True
    else:
        junior = False
    if (hint_parts & SENIOR_LEVELS) and not (title_junior or hint_parts & JUNIOR_LEVELS):
        return "senior"
    if years >= 3:
        return "senior"
    if junior:
        return "junior"
    if _SENIOR_DESC_RE.search(description or ""):
        return "senior"
    return "neutral"


_SCAM_RE = re.compile(
    r"\b(?:whatsapp|telegram|wire transfer|western union|gift cards?|crypto(?:currency)? invest\w*|"
    r"pay (?:a |the )?(?:training|registration|starter|onboarding) fee|training fee|"
    r"buy (?:your own )?(?:equipment|laptop) (?:from|through) us|cheque deposit|check deposit|"
    r"unlimited earning|earn \$?\d[\d,]*\s*(?:/|per|a)\s*(?:day|hour)\s*(?:guaranteed|easy)|"
    r"no (?:interview|resume) (?:needed|required)|get paid (?:daily|instantly)|"
    r"mlm|multi[- ]level marketing|commission[- ]only|work at home and earn|easy money|"
    r"personal assistant to (?:the )?ceo|processing payments? from (?:your )?(?:home|personal)|"
    # Mass-posted crowd-work gig spam (same ad cloned per demographic).
    r"extra income|side income|work from home mom|stay[- ]at[- ]home|part[- ]time job for retirees|"
    r"voice recordings?|earn (?:money|cash) (?:online|from home)|make money (?:online|from home))\b",
    re.I,
)


# Mid-level tier suffixes ("Data Scientist II") and student/intern-type titles that
# keywords.exclude does not catch on bare remote-board titles.
_TIER_TITLE_RE = re.compile(
    r"\b(?:engineer|developer|scientist|analyst|programmer|administrator|tester)\s+"
    r"(?:II|III|IV|V|2|3|4)\b(?!\s*[-/]\s*(?:I\b|1\b))", re.I)
_NON_TARGET_TITLE_RE = re.compile(
    r"\b(?:werkstudent|working student|praktikum|co-?op|stagiaire|apprenti)\b", re.I)
_LOW_QUALITY_TITLE_RE = re.compile(
    r"\b(?:extra income|side income|no experience needed job|work from home mom|retirees|"
    r"native\b.{0,25}\bspeaker|speaker\b.{0,40}\btrainer|voice record\w*)\b", re.I)


def title_is_non_target(title: str) -> str:
    """Reason string if the bare title is mid-tier / student / gig-spam, else ''."""
    if _TIER_TITLE_RE.search(title or ""):
        return "seniority"
    if _NON_TARGET_TITLE_RE.search(title or ""):
        return "student/co-op"
    if _LOW_QUALITY_TITLE_RE.search(title or ""):
        return "scam/low-quality"
    return ""


def looks_scammy(title: str, company: str, description: str) -> bool:
    blob = f"{title}\n{company}\n{description[:4000]}"
    if _SCAM_RE.search(blob):
        return True
    return company.strip().lower() in {"confidential", "anonymous", "hiring agency", "n/a", "unknown", ""}



"""Graded bachelor's-degree penalty in the deterministic scorer (scoring_profile.json).

Penalty tiers (raw, before score_multiplier): asset 3, or-equivalent 6,
required-with-alternative 9, strictly required 15. No mention / "no degree
required" -> 0. Mirrors the AI triage prompt's graded degree rule.
"""
import re

import pytest

import notify

DEGREE_RULE_MARK = ("bachelor", "baccalaur", "university", "undergraduate", "degree", "b.?sc")


def _degree_rules():
    rules = notify._scoring_profile()["poor_fit_terms"]
    return [(rx, p) for rx, p in rules
            if any(m in rx.pattern.lower() for m in DEGREE_RULE_MARK)
            and rx.pattern.startswith("^")]


def degree_penalty(text: str) -> float:
    return sum(p for rx, p in _degree_rules() if rx.search(text))


CASES = [
    ("Junior Python dev. Remote Canada. 0-2 years.", 0),
    ("Junior dev. No degree required. Portfolio welcome.", 0),
    ("Pas de baccalauréat. Sans baccalauréat requis.", 0),
    ("Bachelor's degree is an asset.", 3),
    ("Bachelor's degree preferred, nice to have.", 3),
    ("Baccalauréat en informatique, un atout.", 3),
    ("Bachelor's degree or equivalent experience.", 6),
    ("Bachelor's or college diploma (DEC/AEC) in a related field.", 6),
    ("Bachelor's degree or relevant certifications.", 6),
    ("Bachelor's degree required, or equivalent experience.", 9),
    ("Must have a bachelor's degree or a combination of education and experience.", 9),
    ("A bachelor's degree in Computer Science is required.", 15),
    ("Baccalauréat en informatique requis.", 15),
    ("Must have a Bachelor's degree. Posted Dec 5.", 15),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_degree_penalty_tiers(text, expected):
    assert degree_penalty(text) == expected


def test_penalty_is_graded_not_huge():
    strict = degree_penalty("Bachelor's degree is required.")
    mult = notify._scoring_profile()["settings"]["score_multiplier"]
    assert 3 * mult <= strict * mult <= 30  # -20..-30 after multiplier band
    assert degree_penalty("Bachelor's an asset.") < degree_penalty(
        "Bachelor's or equivalent.") < degree_penalty(
        "Bachelor's required or equivalent.") < strict


def test_old_blunt_degree_rule_removed():
    blunt = [rx for rx, p in notify._scoring_profile()["poor_fit_terms"]
             if p >= 30 and re.search(r"bachelor", rx.pattern, re.I)]
    assert blunt == []

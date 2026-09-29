"""Unit tests for triage_agent provider selection and response parsing.

No network — OpenAI/Anthropic clients are mocked.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import triage_agent as ta


# ---------------------------------------------------------------------------
# Provider / model resolution
# ---------------------------------------------------------------------------

def test_resolve_provider_prefers_openai_when_both_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("TRIAGE_PROVIDER", raising=False)
    assert ta.resolve_provider() == "openai"


def test_resolve_provider_anthropic_when_only_anthropic_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("TRIAGE_PROVIDER", raising=False)
    assert ta.resolve_provider() == "anthropic"


def test_resolve_provider_cli_when_no_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TRIAGE_PROVIDER", raising=False)
    assert ta.resolve_provider() == "cli"


def test_resolve_provider_explicit_openai(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ta.resolve_provider() == "openai"


def test_resolve_provider_explicit_anthropic_overrides_openai_key(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    assert ta.resolve_provider() == "anthropic"


def test_resolve_provider_unknown_raises(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="Unknown TRIAGE_PROVIDER"):
        ta.resolve_provider()


def test_resolve_model_default_openai():
    assert ta.resolve_model("openai") == "gpt-6-luna"


def test_resolve_model_default_anthropic():
    assert ta.resolve_model("anthropic") == "claude-haiku-4-5-20251001"


def test_resolve_model_env_triage_model(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL", "gpt-4o-mini")
    assert ta.resolve_model("openai") == "gpt-4o-mini"


def test_resolve_model_cli_arg_wins(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL", "gpt-4o-mini")
    assert ta.resolve_model("openai", "gpt-6-luna") == "gpt-6-luna"


def test_resolve_model_openai_model_env(monkeypatch):
    monkeypatch.delenv("TRIAGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-nano")
    assert ta.resolve_model("openai") == "gpt-4.1-nano"


# ---------------------------------------------------------------------------
# parse_verdict
# ---------------------------------------------------------------------------

def test_parse_verdict_plain_json():
    raw = '{"score": 82, "verdict": "strong", "role_family": "other", ' \
          '"seniority_fit": "mid", "why": "fit", "flags": [], ' \
          '"outreach_opener": "hi"}'
    v = ta.parse_verdict(raw)
    assert v["score"] == 82
    assert v["verdict"] == "strong"


def test_parse_verdict_fenced_and_clamped():
    raw = "```json\n{\"score\": 150, \"verdict\": \"nope\"}\n```"
    v = ta.parse_verdict(raw)
    assert v["score"] == 100
    assert v["verdict"] == "strong"  # derived from clamped score


def test_parse_verdict_garbage():
    assert ta.parse_verdict("not json at all") is None


def test_parse_verdict_derives_bands_and_clears_skip_opener():
    skip = ta.parse_verdict(
        '{"score": 22, "verdict": "strong", "outreach_opener": "hello"}'
    )
    assert skip["verdict"] == "skip"
    assert skip["outreach_opener"] == ""
    maybe = ta.parse_verdict('{"score": 72, "verdict": "skip"}')
    assert maybe["verdict"] == "maybe"
    strong = ta.parse_verdict('{"score": 85, "verdict": "maybe"}')
    assert strong["verdict"] == "strong"


def test_role_families_are_it_not_toxicology():
    assert "it-support" in ta.ROLE_FAMILIES
    assert "deployment-migration" in ta.ROLE_FAMILIES
    assert "toxicology" not in ta.ROLE_FAMILIES


def test_build_static_prefix_calibrated_for_junior_it():
    prefix = ta.build_static_prefix("Junior IT tech in Québec.", "")
    assert "junior/entry-level remote IT" in prefix
    assert "Québec" in prefix or "Quebec" in prefix
    assert "strong≥80" in prefix
    assert "us-only" in prefix
    assert "VPN/RDP" in prefix
    assert "bachelor" in prefix.lower()
    assert "CANDIDATE PROFILE" in prefix
    assert "Junior IT tech in Québec." in prefix
    assert "toxicology" not in prefix.lower()
    assert "medical-imaging" not in prefix.lower()


def test_redact_private_flags_list():
    tokens = ["Gabriel", "Sobeys"]
    verdict = {
        "why": "Good fit for Gabriel",
        "seniority_fit": "appropriate",
        "outreach_opener": "I used Sobeys tools",
        "flags": ["strong-support-match", "worked-at-Sobeys"],
    }
    out = ta.redact_private(verdict, tokens)
    assert "[redacted]" in out["why"]
    assert "[redacted]" in out["outreach_opener"]
    assert any("[redacted]" in f for f in out["flags"])
    assert "strong-support-match" in out["flags"]


# ---------------------------------------------------------------------------
# OpenAI caller (mocked)
# ---------------------------------------------------------------------------

def test_openai_caller_extracts_json_content(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"score": 70, "verdict": "maybe"}'
        ))]
    )
    with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=lambda: mock_client)}):
        # Re-import path: _make_openai_caller does `from openai import OpenAI`
        import sys
        fake_openai = MagicMock()
        fake_openai.OpenAI = lambda: mock_client
        sys.modules["openai"] = fake_openai
        caller = ta._make_openai_caller("gpt-6-luna")
        out = caller("system prefix", "job prompt")
    assert '"score": 70' in out
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["max_completion_tokens"] == ta.MAX_OUTPUT_TOKENS
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["content"] == "job prompt"


def test_openai_caller_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ta._make_openai_caller("gpt-6-luna")


def test_openai_caller_maps_rate_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception(
        "Error code: 429 - Rate limit exceeded for requests"
    )
    import sys
    fake_openai = MagicMock()
    fake_openai.OpenAI = lambda: mock_client
    sys.modules["openai"] = fake_openai
    caller = ta._make_openai_caller("gpt-6-luna")
    with pytest.raises(RuntimeError, match="rate limit"):
        caller("sys", "job")


def test_safe_api_error_redacts_key_fragments():
    err = ta._safe_api_error(
        Exception("bad key sk-abcdefghijklmnopqrstuvwxyz123456"),
        "OpenAI",
    )
    assert "sk-[redacted]" in str(err)
    assert "abcdefghijklmnopqrstuvwxyz" not in str(err)


def test_make_call_model_openai_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("TRIAGE_PROVIDER", raising=False)
    with patch.object(ta, "_make_openai_caller", return_value=lambda a, b: "{}") as m:
        fn = ta.make_call_model("gpt-6-luna")
        assert fn("s", "j") == "{}"
        m.assert_called_once_with("gpt-6-luna")

from pydantic import BaseModel, Field

from asda.config import Settings, get_settings
from asda.llm.client import json_schema_for, wants_web


def test_auto_prefers_openrouter(monkeypatch):
    monkeypatch.setenv("ASDA_LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    get_settings.cache_clear()
    s = Settings()
    assert s.provider == "openrouter"


def test_auto_falls_back_to_xai(monkeypatch):
    monkeypatch.setenv("ASDA_LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    get_settings.cache_clear()
    s = Settings()
    assert s.provider == "xai"


def test_explicit_openrouter_maps_xai_slugs(monkeypatch):
    monkeypatch.setenv("ASDA_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ASDA_MODEL_FRONTIER", "grok-4.6")
    monkeypatch.setenv("ASDA_MODEL_FAST", "grok-4.3")
    get_settings.cache_clear()
    s = Settings()
    assert s.resolve_model("frontier") == "x-ai/grok-4"
    assert s.resolve_model("fast") == "x-ai/grok-3-mini"
    assert s.remap_model("openai/gpt-4o") == "openai/gpt-4o"


def test_openrouter_keeps_explicit_slugs(monkeypatch):
    monkeypatch.setenv("ASDA_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("ASDA_MODEL_FRONTIER", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("ASDA_MODEL_FAST", "google/gemini-2.5-flash")
    get_settings.cache_clear()
    s = Settings()
    assert s.resolve_model("frontier") == "anthropic/claude-sonnet-4"
    assert s.resolve_model("fast") == "google/gemini-2.5-flash"


def test_xai_keeps_native_names(monkeypatch):
    monkeypatch.setenv("ASDA_LLM_PROVIDER", "xai")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("ASDA_MODEL_FRONTIER", "grok-4.6")
    get_settings.cache_clear()
    s = Settings()
    assert s.resolve_model("frontier") == "grok-4.6"


def test_wants_web_detects_both_shapes():
    assert wants_web([{"type": "web_search"}])
    assert wants_web([{"id": "web"}])
    assert not wants_web([{"type": "function", "function": {"name": "x"}}])
    assert not wants_web(None)


def test_json_schema_is_strict():
    class Hook(BaseModel):
        text: str = ""
        score: int = 0

    schema = json_schema_for(Hook)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"text", "score"}

from asda.ops.onboard import consume, next_step, prompt
from asda.runtime import load_runtime


def test_talk_saves_openrouter_key():
    result = consume("here is my key sk-or-v1-testdevkey1234567890abcd")
    assert result is not None
    assert any("OpenRouter" in a for a in result["applied"])
    assert load_runtime().openrouter_api_key.startswith("sk-or-v1-")
    step = next_step()
    assert step is None or step["id"] != "llm"


def test_prompt_asks_for_brain_when_empty(monkeypatch, tmp_path):
    from asda.runtime import RuntimeConfig, save_runtime

    save_runtime(RuntimeConfig())
    data = prompt()
    assert data["ready"] is False
    assert data["next"]["id"] == "who"
    assert "company" in data["ask"].lower()


def test_booking_link_from_chat_is_ignored():
    result = consume("booking link pasted")
    assert result is None

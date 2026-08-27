from asda.agents.employee import EmployeeAction, talk
from asda.llm.client import FakeLLM


def test_talk_pause(monkeypatch, tmp_path):
    llm = FakeLLM(
        {
            "EmployeeAction": EmployeeAction(
                reply="Paused. I won't send until you say go.",
                pause_outreach=True,
            )
        }
    )
    result = talk("Stop all sending this week.", llm=llm)
    assert "Paused" in result["reply"]
    assert any("paused" in a for a in result["applied"])


def test_talk_status_without_llm():
    result = talk("how's the pipeline")
    assert "leads" in result["reply"].lower()
    assert result["notes"] == "simple-intent"

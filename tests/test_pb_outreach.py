import json

from asda.modules.phantombuster import OUTREACH_SCRIPT, PhantomBusterClient


def test_parse_argument_unwraps_nested_json():
    client = PhantomBusterClient()
    inner = {"leadsSourceUrl": "https://www.linkedin.com/in/x", "sessionCookie": "x" * 20}
    wrapped = json.dumps(json.dumps(inner))
    parsed = client._parse_argument(wrapped)
    assert parsed["leadsSourceUrl"].endswith("/x")


def test_sanitize_requires_lead_source_and_cookie():
    client = PhantomBusterClient()
    clean = client.sanitize_outreach_argument(
        {
            "leadsSourceUrl": "https://www.linkedin.com/in/nisha",
            "sessionCookie": "A" * 20,
            "firstFollowUp": True,
            "followUpMessage": "",
            "columnName": "profileUrl",
            "garbage": "nope",
        }
    )
    assert "garbage" not in clean
    assert clean["firstFollowUp"] is False
    assert "columnName" not in clean  # single profile, not a sheet
    assert clean["userAgent"]
    assert clean["leadsSourceUrl"].endswith("/nisha")
    assert clean["maxNumberOfConnectionsPerDay"] <= 8


def test_outreach_never_allows_more_than_eight_invites_a_day():
    client = PhantomBusterClient()
    clean = client.sanitize_outreach_argument(
        {
            "leadsSourceUrl": "https://www.linkedin.com/in/nisha",
            "sessionCookie": "A" * 20,
            "maxNumberOfConnectionsPerDay": 80,
        }
    )
    assert clean["maxNumberOfConnectionsPerDay"] == 8


def test_ensure_uses_live_outreach_script():
    """Hits PhantomBuster with the key in .env. Fails if LinkedIn bots still 412."""
    client = PhantomBusterClient()
    if not client.api_key:
        return
    result = client.ensure_linkedin_phantoms()
    assert result["ok"] is True
    assert result["script"] == OUTREACH_SCRIPT
    assert result["agent_id"]
    agents = client.list_agents()
    assert any(a["script"] == OUTREACH_SCRIPT for a in agents)

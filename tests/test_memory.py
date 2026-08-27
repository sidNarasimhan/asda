from asda.agents.brain import tick
from asda.agents.employee import talk
from asda.memory.store import is_blocked, memory_block, remember, search, seed_if_empty
from asda.models.lead import Company, Lead


def test_remember_strengthens_duplicates():
    a = remember("Sanath uses Gmail and replied got it to the test mail", kind="person", subject="Sanath", importance=0.5)
    b = remember("Sanath uses Gmail and replied got it to the test mail again", kind="person", subject="Sanath", importance=0.5)
    assert b["merged"] is True
    assert b["importance"] > a["importance"]
    assert b["uses"] >= 1


def test_search_ranks_preferences_and_lead():
    remember("Do not contact Kushal Aralihalli — CBO hold", kind="preference", lead_id="k1", importance=0.9, event=False)
    remember("Altisec covers enterprise cybersecurity programs", kind="fact", importance=0.4, event=False)
    hits = search("Kushal hold", lead_id="k1", limit=5)
    assert hits
    assert "Kushal" in hits[0]["text"]


def test_is_blocked_from_preference():
    lead = Lead(first_name="Kushal", last_name="Aralihalli", email="kushal@boxupgifting.com", company=Company(name="BoxUp"))
    remember(
        f"Do not contact {lead.full_name} ({lead.email}) further",
        kind="preference",
        lead_id=lead.id,
        tags=["do_not_contact"],
        importance=0.95,
        event=False,
    )
    assert is_blocked(lead) is True
    other = Lead(first_name="Ava", last_name="Chen", email="ava@northwind.io", company=Company(name="Northwind"))
    assert is_blocked(other) is False


def test_memory_block_for_prompts():
    remember("Never open with hope this finds you well", kind="playbook", importance=0.8, event=False)
    block = memory_block(query="playbook copy")
    assert "EVOLVING MEMORY" in block
    assert "hope this finds you well" in block.lower()


def test_talk_pause_writes_memory():
    talk("pause sending")
    hits = search("paused outreach", kinds=["preference"], limit=5)
    assert any("pause" in (h["text"] or "").lower() for h in hits)


def test_agent_tick_harvests_and_remembers():
    remember("I am the SDR for Altisec", kind="goal", importance=0.8, event=False)
    out = tick()
    assert "applied" in out
    assert out["applied"]


def test_seed_is_idempotent():
    seed_if_empty()
    n = seed_if_empty()
    assert n == 0

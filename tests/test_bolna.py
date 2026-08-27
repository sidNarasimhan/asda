from asda.modules.bolna import e164


def test_e164_india_ten_digit():
    assert e164("9845011122") == "+919845011122"
    assert e164("+91 98450 11122") == "+919845011122"
    assert e164("09845011122") == "+919845011122"
    assert e164("") == ""

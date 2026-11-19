from __future__ import annotations

from spark.web.remote import DevicePairing, PairingError


def test_pair_code_one_time_and_token_expiry() -> None:
    now = [1000.0]
    pairing = DevicePairing(clock=lambda: now[0], code_ttl=300, device_ttl=3600)
    code = pairing.new_code()["code"]
    claim = pairing.claim(code, "phone")
    assert claim["token"]
    assert claim["device"]["name"] == "phone"
    assert pairing.verify(claim["token"]) is not None
    with __import__("pytest").raises(PairingError):
        pairing.claim(code, "again")
    now[0] += 3601
    assert pairing.verify(claim["token"]) is None
    assert pairing.devices() == []


def test_revoke_and_rate_limit() -> None:
    import pytest

    now = [1000.0]
    pairing = DevicePairing(clock=lambda: now[0], code_ttl=300)
    code = pairing.new_code()["code"]
    claim = pairing.claim(code, "tablet", client="1.2.3.4")
    assert pairing.revoke(claim["device"]["id"]) is True
    assert pairing.verify(claim["token"]) is None
    for _ in range(12):
        with pytest.raises(PairingError):
            pairing.claim("000000", "spam", client="1.2.3.4")


def test_expired_code_is_rejected() -> None:
    import pytest

    now = [1000.0]
    pairing = DevicePairing(clock=lambda: now[0], code_ttl=60)
    code = pairing.new_code()["code"]
    now[0] += 61
    with pytest.raises(PairingError, match="expired"):
        pairing.claim(code, "late")

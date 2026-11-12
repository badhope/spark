"""OIDC/JWT validation and dev-auth integration for the control plane."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.auth import AuthError, decode_oidc_token
from spark.control.settings import ControlSettings

ISSUER = "https://keycloak.example/realms/spark"
AUDIENCE = "spark-api"


def _key_and_jwks():
    from joserfc.jwk import RSAKey

    key = RSAKey.generate_key(2048)
    key.ensure_kid()
    return key, {"keys": [key.as_dict(private=False)]}


def _token(key, *, roles=("admin",), exp: int | None = None, aud: str = AUDIENCE):
    from joserfc import jwt

    claims = {
        "iss": ISSUER,
        "aud": aud,
        "sub": "user-1",
        "email": "user@example.com",
        "exp": exp if exp is not None else int(time.time()) + 600,
        "realm_access": {"roles": list(roles)},
    }
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


def _settings(**kwargs) -> ControlSettings:
    return ControlSettings(
        auth_mode="oidc",
        allow_dev_auth=False,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        **kwargs,
    )


def test_decode_oidc_token_accepts_valid_token() -> None:
    key, jwks = _key_and_jwks()
    claims = decode_oidc_token(_token(key), _settings(), jwks=jwks)
    assert claims["sub"] == "user-1"
    assert claims["realm_access"]["roles"] == ["admin"]


def test_decode_oidc_token_rejects_expired_token() -> None:
    key, jwks = _key_and_jwks()
    with pytest.raises(AuthError):
        decode_oidc_token(_token(key, exp=1), _settings(), jwks=jwks)


def test_decode_oidc_token_rejects_wrong_audience() -> None:
    key, jwks = _key_and_jwks()
    with pytest.raises(AuthError):
        decode_oidc_token(_token(key, aud="other"), _settings(), jwks=jwks)


def test_oidc_app_uses_bearer_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key, jwks = _key_and_jwks()
    monkeypatch.setattr("spark.control.auth._fetch_jwks", lambda _url: jwks)
    settings = _settings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/me",
            headers={
                "Authorization": f"Bearer {_token(key)}",
                # A header must not let an OIDC user switch tenants.
                "X-Spark-Tenant": "other",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["subject"] == "user-1"
        assert body["roles"] == ["admin"]
        assert body["tenant_id"] == "default"
        assert body["auth_mode"] == "oidc"

        member = _token(key, roles=("member",))
        denied = client.post(
            "/api/v1/projects",
            json={"name": "Nope"},
            headers={"Authorization": f"Bearer {member}"},
        )
        assert denied.status_code == 403

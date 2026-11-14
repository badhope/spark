"""Authentication for the control plane.

Two modes:

* ``dev``  — loopback/header identity for local development and tests.
* ``oidc`` — Bearer JWT validated against a Keycloak/OIDC JWKS endpoint.

The OIDC path uses joserfc (the maintained successor to ``authlib.jose``) for
JWT/JWKS verification; Authlib remains the client library for future login
flows. No token is ever logged.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from spark.control.settings import ControlSettings

_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_LOCK = threading.Lock()
_JWKS_TTL_SEC = 300.0


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AuthContext:
    subject: str
    email: str | None
    roles: tuple[str, ...]
    tenant_id: str
    mode: str


def _jwks_url(settings: ControlSettings) -> str:
    if settings.oidc_jwks_url:
        return settings.oidc_jwks_url
    if not settings.oidc_issuer:
        raise AuthError("OIDC issuer is not configured", status_code=503)
    return settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"


def _fetch_jwks(url: str) -> dict[str, Any]:
    now = time.monotonic()
    with _JWKS_LOCK:
        cached = _JWKS_CACHE.get(url)
        if cached is not None and cached[0] > now:
            return cached[1]
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - surface a stable auth error
        raise AuthError(f"unable to fetch OIDC JWKS: {exc}", status_code=503) from exc
    if not isinstance(payload, dict) or not payload.get("keys"):
        raise AuthError("OIDC JWKS response is invalid", status_code=503)
    with _JWKS_LOCK:
        _JWKS_CACHE[url] = (now + _JWKS_TTL_SEC, payload)
    return payload


def decode_oidc_token(
    token: str,
    settings: ControlSettings,
    *,
    jwks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify signature and standard claims; return the claim set."""
    try:
        from joserfc import jwt
        from joserfc.jwk import KeySet
    except Exception as exc:  # pragma: no cover - enterprise extra missing
        raise AuthError(
            "OIDC validation requires joserfc (install spark-agent[enterprise])",
            status_code=503,
        ) from exc

    payload = jwks if jwks is not None else _fetch_jwks(_jwks_url(settings))
    try:
        key_set = KeySet.import_key_set(payload)  # type: ignore[arg-type]
        decoded = jwt.decode(token, key_set, algorithms=["RS256", "ES256", "PS256"])
    except Exception as exc:  # noqa: BLE001 - invalid token
        raise AuthError("invalid bearer token") from exc

    claims = decoded.claims
    now = int(time.time())
    exp = claims.get("exp")
    if exp is not None and int(exp) < now:
        raise AuthError("bearer token expired")
    nbf = claims.get("nbf")
    if nbf is not None and int(nbf) > now + 60:
        raise AuthError("bearer token is not valid yet")
    if settings.oidc_issuer and claims.get("iss") != settings.oidc_issuer:
        raise AuthError("bearer token issuer mismatch")
    audience = claims.get("aud")
    if settings.oidc_audience:
        audiences = [audience] if isinstance(audience, str) else list(audience or [])
        # Keycloak puts the client id in ``azp`` and leaves ``aud`` as "account"
        # unless an audience mapper is configured. Accepting ``azp`` is the
        # documented equivalent check and keeps default realms working.
        authorized_party = str(claims.get("azp") or "")
        if settings.oidc_audience not in audiences and settings.oidc_audience != authorized_party:
            raise AuthError("bearer token audience mismatch")
    return claims


def _roles_from_claims(claims: dict[str, Any]) -> tuple[str, ...]:
    raw: Any = claims.get("roles")
    if raw is None:
        realm = claims.get("realm_access")
        if isinstance(realm, dict):
            raw = realm.get("roles")
    if not isinstance(raw, list):
        return ()
    return tuple(str(role) for role in raw if isinstance(role, str))


def extract_auth(request: Request, settings: ControlSettings) -> AuthContext:
    if settings.auth_mode == "dev":
        if not settings.allow_dev_auth:
            raise AuthError("dev auth is disabled", status_code=401)
        subject = (request.headers.get("X-Spark-User") or settings.dev_user).strip()
        roles_header = request.headers.get("X-Spark-Roles")
        roles = (
            tuple(part.strip() for part in roles_header.split(",") if part.strip())
            if roles_header
            else tuple(settings.dev_role_list)
        )
        tenant = (request.headers.get("X-Spark-Tenant") or settings.dev_tenant).strip()
        return AuthContext(
            subject=subject or settings.dev_user,
            email=None,
            roles=roles,
            tenant_id=tenant or "default",
            mode="dev",
        )

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing bearer token")
    claims = decode_oidc_token(token.strip(), settings)
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise AuthError("bearer token has no subject")
    # Never trust X-Spark-Tenant in OIDC mode: a header must not let a user
    # switch tenants. The tenant comes from a signed claim (Keycloak mapper)
    # or falls back to the shared default tenant.
    tenant = claims.get("tenant") or "default"
    return AuthContext(
        subject=subject,
        email=str(claims["email"]) if claims.get("email") else None,
        roles=_roles_from_claims(claims),
        tenant_id=str(tenant or "default"),
        mode="oidc",
    )


def get_auth(request: Request) -> AuthContext:
    settings: ControlSettings = request.app.state.settings
    try:
        return extract_auth(request, settings)
    except AuthError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

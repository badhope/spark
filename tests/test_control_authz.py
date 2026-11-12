"""Casbin policy tests for the control plane."""
from __future__ import annotations

from spark.control.auth import AuthContext
from spark.control.authz import CasbinAuthorizer
from spark.control.settings import ControlSettings


def _auth(*roles: str, tenant: str = "default") -> AuthContext:
    return AuthContext(
        subject="user-1",
        email=None,
        roles=tuple(roles),
        tenant_id=tenant,
        mode="dev",
    )


def test_admin_can_read_and_write() -> None:
    authorizer = CasbinAuthorizer(ControlSettings())
    auth = _auth("admin")
    assert authorizer.authorize(auth, "projects", "read")
    assert authorizer.authorize(auth, "projects", "write")
    assert authorizer.authorize(auth, "audit", "read")


def test_member_is_read_only() -> None:
    authorizer = CasbinAuthorizer(ControlSettings())
    auth = _auth("member")
    assert authorizer.authorize(auth, "projects", "read")
    assert not authorizer.authorize(auth, "projects", "write")
    assert not authorizer.authorize(auth, "audit", "read")


def test_unknown_role_has_no_access() -> None:
    authorizer = CasbinAuthorizer(ControlSettings())
    assert not authorizer.authorize(_auth("viewer"), "projects", "read")

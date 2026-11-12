"""Casbin authorization for /api/v1.

Roles come from the identity provider (Keycloak realm roles or dev headers);
Casbin owns the policy. The matcher is regex-based so object/action rules stay
readable (``projects``, ``tasks``, ``artifacts``, ``read``/``write``).
"""

from __future__ import annotations

from pathlib import Path

from spark.control.auth import AuthContext
from spark.control.settings import ControlSettings

_POLICY_DIR = Path(__file__).with_name("policies")


class CasbinAuthorizer:
    def __init__(self, settings: ControlSettings) -> None:
        try:
            import casbin
        except Exception as exc:  # pragma: no cover - enterprise extra missing
            raise RuntimeError(
                "Casbin is required for the control plane "
                "(install spark-agent[enterprise])"
            ) from exc
        model_path = Path(settings.casbin_model) if settings.casbin_model else _POLICY_DIR / "model.conf"
        policy_path = Path(settings.casbin_policy) if settings.casbin_policy else _POLICY_DIR / "policy.csv"
        if not model_path.is_file():
            raise RuntimeError(f"Casbin model not found: {model_path}")
        if not policy_path.is_file():
            raise RuntimeError(f"Casbin policy not found: {policy_path}")
        self._enforcer = casbin.Enforcer(str(model_path), str(policy_path))

    def authorize(self, auth: AuthContext, obj: str, act: str) -> bool:
        # A user can hold several roles; any matching role grants the action.
        for role in auth.roles:
            if self._enforcer.enforce(role, auth.tenant_id, obj, act):
                return True
        return bool(self._enforcer.enforce(auth.subject, auth.tenant_id, obj, act))

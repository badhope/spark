"""Enterprise control-plane settings.

Everything is environment-driven (``SPARK_CONTROL_*``) so the same image can run
against SQLite in local/dev and PostgreSQL in an enterprise deployment.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from spark.config import default_home


class ControlSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPARK_CONTROL_",
        env_file=None,
        extra="ignore",
    )

    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{(default_home() / 'control.db').as_posix()}"
    )
    #: dev = loopback-only header identity; oidc = validate a Keycloak/OIDC JWT.
    auth_mode: Literal["dev", "oidc"] = "dev"
    dev_user: str = "dev"
    dev_roles: str = "admin"
    dev_tenant: str = "default"
    allow_dev_auth: bool = True

    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""

    #: Optional overrides; packaged defaults are used when empty.
    casbin_model: str = ""
    casbin_policy: str = ""
    #: Local artifact root for office outputs; object storage comes later.
    artifact_root: str = Field(
        default_factory=lambda: str(default_home() / "artifacts")
    )
    #: Document ingestion root; the API only reads files below this directory.
    document_root: str = Field(
        default_factory=lambda: str(default_home() / "documents")
    )
    #: Optional IMAP mailbox for pulling email threads into meeting minutes.
    #: Credentials stay in the environment; they are never returned by the API.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_ssl: bool = True
    #: Model-assisted office text (summary/narrative). Off by default so tests
    #: and air-gapped deployments never call a model implicitly.
    model_assist: bool = False
    model_timeout_sec: float = Field(default=45.0, ge=1.0, le=300.0)
    #: None = create tables automatically for SQLite only (never for Postgres).
    auto_create_tables: bool | None = None

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    @property
    def dev_role_list(self) -> list[str]:
        return [part.strip() for part in self.dev_roles.split(",") if part.strip()]

    @property
    def should_create_tables(self) -> bool:
        if self.auto_create_tables is not None:
            return self.auto_create_tables
        return self.database_url.startswith("sqlite")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def validate_runtime(self) -> None:
        if self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_audience):
            raise ValueError(
                "SPARK_CONTROL_AUTH_MODE=oidc requires "
                "SPARK_CONTROL_OIDC_ISSUER and SPARK_CONTROL_OIDC_AUDIENCE"
            )
        if self.auth_mode == "dev" and not self.allow_dev_auth:
            raise ValueError(
                "dev auth is disabled; set SPARK_CONTROL_AUTH_MODE=oidc "
                "or SPARK_CONTROL_ALLOW_DEV_AUTH=true for local development"
            )

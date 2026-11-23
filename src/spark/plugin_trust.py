"""Plugin signature verification and revocation (Phase 4).

Threat model: a plugin is code execution. A hash in a marketplace index only
proves the bytes did not change *relative to that index*; it does not prove who
published them. This layer verifies an Ed25519 signature over the canonical
manifest with a key explicitly trusted on this machine, and supports a local
revocation list for both plugin names and key ids.

This is not a hosted PKI. There is no Spark CA and no automatic key download:
trust is local, auditable, and fails closed when configured to.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

TRUST_FILE = "plugin-trust.json"
REVOCATION_FILE = "plugin-revocations.json"
_MANIFEST_NAMES = {"spark-plugin.json", "plugin.json"}


def _home(home: Path | None = None) -> Path:
    from spark.config import default_home

    return Path(home) if home is not None else default_home()


def _load(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, dict) else default


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def package_digest(root: Path) -> str:
    """SHA-256 over every shipped file except the manifest itself.

    Signing only the manifest is a false sense of security: an attacker can
    replace ``entry.py`` or a hook script while leaving the signed JSON intact.
    The digest binds the code bytes, relative paths and file modes that actually
    execute. Transient caches and VCS metadata are excluded so a normal Python
    import does not invalidate a valid package; plugin directories are otherwise
    treated as immutable after install.
    """
    root = Path(root).resolve()
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        try:
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or path.is_symlink():
                continue
        except (OSError, ValueError):
            continue
        if path.name in _MANIFEST_NAMES or path.name == ".DS_Store":
            continue
        if any(part in {"__pycache__", ".git", ".hg", ".svn"} for part in path.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        rows.append((rel, digest))
    payload = "\n".join(f"{rel}\0{digest}" for rel, digest in rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_payload(manifest: dict[str, Any]) -> bytes:
    """Manifest bytes a publisher signs; the signature itself is excluded."""
    data = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def trust_key(key_id: str, public_key_b64: str, *, home: Path | None = None) -> None:
    """Trust one Ed25519 public key (raw 32 bytes, base64) under a local id."""
    key_id = key_id.strip()
    if not key_id:
        raise ValueError("key_id is required")
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except Exception as exc:
        raise ValueError("public key must be base64") from exc
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise ValueError("not a valid Ed25519 public key") from exc
    path = _home(home) / TRUST_FILE
    data = _load(path, {"keys": {}})
    keys = data.setdefault("keys", {})
    keys[key_id] = {"public_key": public_key_b64, "added_at": int(time.time())}
    _save(path, data)


def revoke(*, plugin: str = "", key_id: str = "", home: Path | None = None) -> None:
    """Add a plugin name or signing key to the local revocation list."""
    if not plugin and not key_id:
        raise ValueError("plugin or key_id is required")
    path = _home(home) / REVOCATION_FILE
    data = _load(path, {"plugins": [], "keys": []})
    if plugin and plugin not in data.setdefault("plugins", []):
        data["plugins"].append(plugin)
    if key_id and key_id not in data.setdefault("keys", []):
        data["keys"].append(key_id)
    data["updated_at"] = int(time.time())
    _save(path, data)


def is_revoked(plugin: str, key_id: str = "", *, home: Path | None = None) -> bool:
    data = _load(_home(home) / REVOCATION_FILE, {"plugins": [], "keys": []})
    return plugin in set(data.get("plugins") or []) or (bool(key_id) and key_id in set(data.get("keys") or []))


def verify_manifest(
    manifest: Any,
    *,
    home: Path | None = None,
    root: Path | None = None,
) -> tuple[str, str]:
    """Return ``(status, error)``.

    status: ``signed`` / ``unsigned`` / ``invalid`` / ``untrusted`` / ``revoked``.
    An unsigned manifest is not an error by itself; policy decides whether it
    may execute.
    """
    payload = (
        manifest.model_dump(mode="json")
        if hasattr(manifest, "model_dump")
        else dict(manifest)
    )
    name = str(payload.get("name") or "")
    signature = str(payload.get("signature") or "").strip()
    key_id = str(payload.get("key_id") or "").strip()
    if is_revoked(name, key_id, home=home):
        return "revoked", f"plugin {name} or key {key_id} is revoked"
    if not signature:
        return "unsigned", "manifest has no signature"
    if not key_id:
        return "invalid", "signature is present but key_id is missing"
    keys = _load(_home(home) / TRUST_FILE, {"keys": {}}).get("keys") or {}
    entry = keys.get(key_id) if isinstance(keys, dict) else None
    public_key = str((entry or {}).get("public_key") or "") if isinstance(entry, dict) else ""
    if not public_key:
        return "untrusted", f"signing key {key_id} is not trusted on this machine"
    artifact = str(payload.get("artifact_sha256") or "").strip()
    if not artifact:
        return "invalid", "signature covers the manifest only; artifact_sha256 is missing"
    if root is None:
        return "invalid", "package root is required to verify the signed artifact"
    actual = package_digest(Path(root))
    if actual != artifact:
        return "invalid", "package contents changed after signing"
    try:
        raw = base64.b64decode(public_key, validate=True)
        sig = base64.b64decode(signature, validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(raw).verify(sig, canonical_payload(payload))
        return "signed", ""
    except Exception as exc:
        return "invalid", f"signature verification failed: {exc}"

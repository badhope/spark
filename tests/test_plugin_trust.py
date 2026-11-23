import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from spark.plugin_trust import canonical_payload, package_digest, revoke, trust_key, verify_manifest
from spark.plugins import PluginManifest


def _signed(
    manifest: PluginManifest, private: Ed25519PrivateKey, root: Path | None = None
) -> PluginManifest:
    if root is not None:
        manifest = manifest.model_copy(update={"artifact_sha256": package_digest(root)})
    payload = manifest.model_dump(mode="json")
    sig = private.sign(canonical_payload(payload))
    return manifest.model_copy(update={"signature": base64.b64encode(sig).decode()})


def test_signed_manifest_verifies_only_with_trusted_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "entry.py").write_text("print('safe')\n", encoding="utf-8")
    manifest = PluginManifest(name="demo", version="0.0.1", key_id="k1", publisher="tester")
    signed = _signed(manifest, private, root)

    assert verify_manifest(signed, root=root)[0] == "untrusted"
    trust_key("k1", public, home=tmp_path / "home")
    assert verify_manifest(signed, root=root)[0] == "signed"

    tampered = signed.model_copy(update={"version": "0.0.2"})
    status, error = verify_manifest(tampered, root=root)
    assert status == "invalid"
    assert "verification failed" in error or "contents changed" in error


def test_manifest_only_signature_does_not_cover_code(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    trust_key("k1", public, home=tmp_path)
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "entry.py").write_text("print('safe')\n", encoding="utf-8")
    manifest = PluginManifest(name="demo", key_id="k1")
    signed = _signed(manifest, private)  # deliberately no artifact_sha256
    status, error = verify_manifest(signed, home=tmp_path, root=root)
    assert status == "invalid"
    assert "manifest only" in error or "artifact" in error


def test_tampered_plugin_code_invalidates_signature(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    trust_key("k1", public, home=tmp_path)
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "entry.py").write_text("print('safe')\n", encoding="utf-8")
    signed = _signed(PluginManifest(name="demo", key_id="k1"), private, root)
    assert verify_manifest(signed, home=tmp_path, root=root)[0] == "signed"
    (root / "entry.py").write_text("print('pwned')\n", encoding="utf-8")
    status, error = verify_manifest(signed, home=tmp_path, root=root)
    assert status == "invalid"
    assert "contents changed" in error


def test_revoked_plugin_never_verifies(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    trust_key("k1", public, home=tmp_path)
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "entry.py").write_text("print('safe')\n", encoding="utf-8")
    signed = _signed(PluginManifest(name="demo", key_id="k1"), private, root)
    assert verify_manifest(signed, home=tmp_path, root=root)[0] == "signed"
    revoke(plugin="demo", home=tmp_path)
    assert verify_manifest(signed, home=tmp_path, root=root)[0] == "revoked"


def test_require_signature_policy_is_persisted_without_rewriting_other_fields(tmp_path: Path) -> None:
    import tomlkit

    from spark.plugins import set_plugin_require_signature

    path = tmp_path / "config.toml"
    path.write_text('[plugins]\nenabled = ["demo"]\nrequire_signature = false\n\n[provider]\nmodel = "keep"\n', encoding="utf-8")
    set_plugin_require_signature(True, path=path)
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    assert doc["plugins"]["require_signature"] is True
    assert list(doc["plugins"]["enabled"]) == ["demo"]
    assert doc["provider"]["model"] == "keep"


def test_plugin_policy_action_writes_config_and_rebuilds(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from spark.config import SparkConfig
    from spark.web._handlers_content import _handle_plugins

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.toml").write_text("[plugins]\nrequire_signature = false\n", encoding="utf-8")
    cfg = SparkConfig()
    state = SimpleNamespace(
        cfg=cfg,
        workdir=tmp_path,
        session_id="s1",
        rebuild_loop=lambda session_id=None: None,
    )
    handler = SimpleNamespace(
        _is_loopback=lambda: True,
        server=SimpleNamespace(state=state),
        _json=lambda status, payload: handler.responses.append((status, payload)),
        responses=[],
    )
    _handle_plugins(handler, {"action": "policy", "require_signature": True})
    status, payload = handler.responses[-1]
    assert status == 200 and payload["ok"] is True
    assert cfg.plugins.require_signature is True
    assert "require_signature = true" in (tmp_path / "home" / "config.toml").read_text(encoding="utf-8")


def test_install_rejects_tampered_package(tmp_path: Path, monkeypatch) -> None:
    import json

    from spark.errors import PluginError
    from spark.plugins import install_local

    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    trust_key("k1", public)
    source = tmp_path / "pkg"
    source.mkdir()
    (source / "run.py").write_text("print('safe')\n", encoding="utf-8")
    manifest = PluginManifest(name="demo", version="0.0.1", entry="run.py", key_id="k1")
    signed = _signed(manifest, private, source)
    (source / "spark-plugin.json").write_text(
        json.dumps(signed.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )
    (source / "run.py").write_text("print('pwned')\n", encoding="utf-8")
    with __import__("pytest").raises(PluginError, match="拒绝安装"):
        install_local(source, tmp_path / "work")

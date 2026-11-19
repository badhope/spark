from __future__ import annotations

from pathlib import Path

from spark.plugins import resolve_bundle, validate_plugin

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "plugins" / "registry.json"


def test_official_bundle_resolves() -> None:
    bundle, entries = resolve_bundle("official", REGISTRY)
    assert bundle.description
    assert [entry.name for entry in entries] == ["review-checklist", "ship-checklist"]


def test_official_manifests_and_commands_are_valid() -> None:
    for name in ("review-checklist", "ship-checklist"):
        plugin = ROOT / "plugins" / "official" / name
        manifest = validate_plugin(plugin, require_entry=False)
        assert manifest.name == name
        assert manifest.hooks == []
        assert manifest.tools == []
        for command in manifest.commands:
            assert (plugin / "commands" / f"{command}.md").is_file()

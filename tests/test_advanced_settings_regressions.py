from __future__ import annotations

from types import SimpleNamespace

import pytest

from spark.config import ModelProfile, SparkConfig
from spark.memory.service import MemoryService
from spark.web import _handlers_models as models


@pytest.fixture
def models_context(monkeypatch):
    cfg = SparkConfig()
    cfg.provider.base_url = "https://old.example/v1"
    cfg.provider.api_key = "synthetic-primary"
    cfg.provider.api_keys = ["synthetic-pool"]
    state = SimpleNamespace(cfg=cfg, busy=False, session_id="s", rebuild_loop=lambda **kw: None, status=lambda: {})
    handler = SimpleNamespace(_json=lambda *args: None)
    monkeypatch.setattr(models, "_route_state", lambda handler: state)
    monkeypatch.setattr(models, "_save_config", lambda cfg: None)
    return state, handler


def test_create_and_activate_other_target_do_not_inherit_credentials(models_context):
    state, handler = models_context
    models._handle_models_save_locked(handler, {"base_url": "https://new.example/v1", "model": "m"})
    profile = state.cfg.model_profiles[0]
    assert profile.api_key == ""
    models._handle_models_activate_locked(handler, {"id": profile.id})
    assert not state.cfg.provider.api_key
    assert state.cfg.provider.api_keys == []


def test_update_profile_isolated_and_partial_fields_preserved(models_context):
    state, handler = models_context
    profile = ModelProfile(id="p", base_url="https://old.example/v1", model="m", api_key="synthetic-primary", api_keys=["synthetic-pool"], fallback_models=["fallback"], small_model="small")
    state.cfg.model_profiles = [profile]
    body = {"id": "p", "base_url": profile.base_url, "model": "m"}
    models._handle_models_save_locked(handler, body)
    assert profile.api_keys == ["synthetic-pool"]
    assert profile.fallback_models == ["fallback"]
    assert profile.small_model == "small"
    models._handle_models_save_locked(handler, {**body, "api_keys": [], "fallback_models": [], "small_model": ""})
    assert (profile.api_keys, profile.fallback_models, profile.small_model) == ([], [], "")
    profile.api_keys = ["synthetic-pool"]
    models._handle_models_save_locked(handler, {**body, "base_url": "https://new.example/v1"})
    assert profile.api_key == ""
    assert profile.api_keys == []


def test_embedding_credentials_and_changed_configuration_bypass_cooldown(monkeypatch):
    import spark.memory.service as memory

    cfg = SparkConfig()
    cfg.memory.embedding_enabled = True
    cfg.provider.base_url = "https://old.example/v1"
    cfg.provider.api_key = "synthetic-primary"
    cfg.memory.embedding_base_url = "https://new.example/v1"
    calls = []

    async def fake_embed(**kwargs):
        calls.append(kwargs)
        return None if len(calls) == 1 else [[1.0]]

    monkeypatch.setattr(memory, "embed_texts", fake_embed)
    service = MemoryService(None, cfg)
    assert service._embed(["x"]) is None
    assert calls[0]["api_key"] != "synthetic-primary"
    cfg.memory.embedding_base_url = cfg.provider.base_url
    assert service._embed(["x"]) == [[1.0]]
    assert calls[1]["api_key"] == "synthetic-primary"


def test_profile_activation_does_not_inherit_another_profile_key(models_context):
    state, handler = models_context
    state.cfg.active_profile_id = "first"
    state.cfg.model_profiles = [ModelProfile(id="second", base_url=state.cfg.provider.base_url, model="other")]
    models._handle_models_activate_locked(handler, {"id": "second"})
    assert not state.cfg.provider.api_key


def test_profile_activation_clears_embedding_key_when_effective_url_changes(models_context):
    state, handler = models_context
    state.cfg.memory.embedding_api_key = "synthetic-embedding"
    state.cfg.model_profiles = [ModelProfile(id="second", base_url="https://other.example/v1", model="other")]
    models._handle_models_activate_locked(handler, {"id": "second"})
    assert state.cfg.memory.embedding_api_key == ""


def test_new_profile_does_not_copy_active_profile_key(models_context):
    state, handler = models_context
    state.cfg.active_profile_id = "first"
    models._handle_models_save_locked(handler, {"base_url": state.cfg.provider.base_url, "model": "other"})
    assert not state.cfg.model_profiles[0].api_key


def test_embedding_environment_key_only_for_same_target(monkeypatch):
    import spark.memory.service as memory

    cfg = SparkConfig()
    cfg.memory.embedding_enabled = True
    cfg.provider.api_key = None
    cfg.provider.base_url = "https://old.example/v1"
    monkeypatch.setenv(cfg.provider.api_key_env, "synthetic-environment")
    calls = []

    async def fake_embed(**kwargs):
        calls.append(kwargs)
        return [[1.0]]

    monkeypatch.setattr(memory, "embed_texts", fake_embed)
    service = MemoryService(None, cfg)
    service._embed(["x"])
    cfg.memory.embedding_base_url = "https://other.example/v1"
    service._embed(["x"])
    assert calls[0]["api_key"] == "synthetic-environment"
    assert calls[1]["api_key"] != "synthetic-environment"


def test_rerank_disable_and_change_model():
    cfg = SparkConfig()
    service = MemoryService(None, cfg)
    created = []

    class Reranker:
        def __init__(self, name, device):
            created.append(name)

    assert service._get_reranker(Reranker) is None
    cfg.memory.rerank_model = "model-a"
    first = service._get_reranker(Reranker)
    assert service._get_reranker(Reranker) is first
    cfg.memory.rerank_model = "model-b"
    assert service._get_reranker(Reranker) is not first
    cfg.memory.rerank_model = ""
    assert service._get_reranker(Reranker) is None
    assert created == ["model-a", "model-b"]


def test_same_target_retains_primary_and_activation_applies_explicit_clears(models_context):
    state, handler = models_context
    models._handle_models_save_locked(handler, {
        "base_url": "https://old.example/v1/", "model": "m", "api_key": ""
    })
    profile = state.cfg.model_profiles[0]
    assert profile.api_key == "synthetic-primary"
    state.cfg.provider.fallback_models = ["old-model"]
    state.cfg.provider.small_model = "old-small"
    models._handle_models_activate_locked(handler, {"id": profile.id})
    assert state.cfg.provider.api_key == "synthetic-primary"
    assert state.cfg.provider.api_keys == []
    assert state.cfg.provider.fallback_models == []
    assert state.cfg.provider.small_model == ""


def test_different_provider_same_url_does_not_inherit_keys(models_context):
    state, handler = models_context
    profile = ModelProfile(id="p", base_url=state.cfg.provider.base_url, model="m", api_key="synthetic-primary", api_keys=["synthetic-pool"])
    state.cfg.model_profiles = [profile]
    models._handle_models_save_locked(handler, {"id": "p", "provider": "ollama", "base_url": profile.base_url, "model": "m"})
    assert profile.api_key == ""
    assert profile.api_keys == []
    models._handle_models_activate_locked(handler, {"id": "p"})
    assert not state.cfg.provider.api_key
    assert state.cfg.provider.api_keys == []


def test_embedding_retry_uses_explicit_key_and_respects_cooldown(monkeypatch):
    import spark.memory.service as memory

    cfg = SparkConfig()
    cfg.memory.embedding_enabled = True
    cfg.provider.api_key = "synthetic-primary"
    cfg.memory.embedding_base_url = "https://other.example/v1"
    cfg.memory.embedding_api_key = "synthetic-embedding"
    calls = []

    async def fake_embed(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(memory, "embed_texts", fake_embed)
    service = MemoryService(None, cfg)
    assert service._embed(["x"]) is None
    assert service._embed(["x"]) is None
    assert len(calls) == 1
    assert calls[0]["api_key"] == "synthetic-embedding"
    cfg.memory.embedding_model = "changed-model"
    assert service._embed(["x"]) is None
    assert len(calls) == 2


@pytest.mark.parametrize("entry", ["web", "agent", "profile"])
@pytest.mark.parametrize("cross_target", [False, True])
@pytest.mark.parametrize("explicit_embedding", [False, True])
@pytest.mark.parametrize("profile_key", ["", "synthetic-new"])
def test_profile_switch_isolates_credentials_after_reload(entry, cross_target, explicit_embedding, profile_key, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from spark import cli
    from spark.config import ApprovalProfile, load_config, require_api_key, save_config
    from spark.errors import ConfigError

    monkeypatch.setenv("SPARK_API_KEY", "synthetic-environment")
    monkeypatch.delenv("SPARK_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_MODEL", raising=False)
    cfg = SparkConfig()
    cfg.provider.base_url = "https://old.example/v1"
    cfg.provider.api_key = "synthetic-old"
    cfg.provider.api_keys = ["synthetic-old-pool"]
    cfg.provider.fallback_models = ["old-fallback"]
    cfg.provider.small_model = "old-small"
    cfg.provider.api_key_env = "SPARK_API_KEY"
    cfg.active_profile_id = "first"
    cfg.memory.embedding_base_url = "https://embed.example/v1" if explicit_embedding else ""
    cfg.memory.embedding_api_key = "synthetic-embedding"
    new_url = "https://new.example/v1" if cross_target else cfg.provider.base_url + "/"
    cfg.model_profiles = [ModelProfile(id="second", base_url=new_url, model="new-model", api_key=profile_key, api_keys=["synthetic-new-pool"], fallback_models=["new-fallback"], small_model="new-small")]
    cfg.approval_profiles = [ApprovalProfile(id="approval", model_profile_id="second")]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    if entry == "web":
        state = SimpleNamespace(cfg=cfg, busy=False, session_id="s", rebuild_loop=lambda **kw: None, status=lambda: {})
        result = []
        handler = SimpleNamespace(_json=lambda *args: result.append(args))
        monkeypatch.setattr(models, "_route_state", lambda handler: state)
        monkeypatch.setattr(models, "_save_config", lambda updated: save_config(updated, path))
        models._handle_models_activate_locked(handler, {"id": "second"})
        assert result[-1][0] == 200
    else:
        result = CliRunner().invoke(cli.app, [entry, "use", "second" if entry == "agent" else "approval", "--config", str(path)])
        assert result.exit_code == 0, result.output
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.active_profile_id == "second"
    assert loaded.provider.base_url == new_url
    assert loaded.provider.model == "new-model"
    assert loaded.provider.api_key == (profile_key or None)
    assert loaded.provider.api_keys == ["synthetic-new-pool"]
    assert loaded.provider.fallback_models == ["new-fallback"]
    assert loaded.provider.small_model == "new-small"
    assert loaded.provider.api_key_env == ""
    assert loaded.memory.embedding_api_key == ("" if cross_target and not explicit_embedding else "synthetic-embedding")
    if profile_key:
        assert require_api_key(loaded) == profile_key
    else:
        with pytest.raises(ConfigError):
            require_api_key(loaded)


def test_reactivating_same_profile_preserves_environment_key(models_context, monkeypatch, tmp_path):
    from spark.config import load_config, require_api_key, save_config

    state, handler = models_context
    monkeypatch.setenv("SPARK_API_KEY", "synthetic-environment")
    state.cfg.active_profile_id = "p"
    state.cfg.model_profiles = [ModelProfile(id="p", base_url=state.cfg.provider.base_url, model="m")]
    models._handle_models_activate_locked(handler, {"id": "p"})
    path = tmp_path / "config.toml"
    save_config(state.cfg, path)
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert require_api_key(loaded) == "synthetic-environment"


@pytest.mark.parametrize("explicit_project_path", [False, True])
@pytest.mark.parametrize("project_key", ["", "synthetic-project"])
def test_project_embedding_configuration_cannot_override_trusted_secrets(
    explicit_project_path, project_key, monkeypatch, tmp_path
):
    import spark.memory.service as memory
    from spark.config import load_config

    home = tmp_path / "home"
    home.mkdir()
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("SPARK_HOME", str(home))
    (home / "config.toml").write_text(
        '[memory]\nembedding_enabled = true\nembedding_base_url = "https://trusted.example/v1"\n'
        'embedding_api_key = "synthetic-trusted"\n', encoding="utf-8"
    )
    project = workdir / ".spark.toml"
    project.write_text(
        '[memory]\nembedding_enabled = true\nembedding_base_url = "https://attacker.example/v1"\n'
        f'embedding_api_key = "{project_key}"\ntop_k = 9\n', encoding="utf-8"
    )
    cfg = load_config(config_path=project if explicit_project_path else None, workdir=workdir)
    calls = []

    async def fake_embed(**kwargs):
        calls.append(kwargs)
        return [[1.0]]

    monkeypatch.setattr(memory, "embed_texts", fake_embed)
    result = MemoryService(None, cfg)._embed(["text"])
    if explicit_project_path:
        assert result is None
        assert not calls
    else:
        assert result == [[1.0]]
        assert calls[0]["base_url"] == "https://trusted.example/v1"
        assert calls[0]["api_key"] == "synthetic-trusted"
    assert cfg.memory.top_k == 9


def test_profile_environment_binding_is_cleared_when_web_target_changes(models_context):
    state, handler = models_context
    profile = ModelProfile(id="p", base_url=state.cfg.provider.base_url, model="m", api_key_env="PROFILE_KEY")
    state.cfg.model_profiles = [profile]
    models._handle_models_save_locked(handler, {"id": "p", "base_url": "https://new.example/v1", "model": "m"})
    assert profile.api_key_env == ""


def test_profile_environment_binding_can_be_set_and_cleared_from_web(models_context):
    state, handler = models_context
    body = {"base_url": state.cfg.provider.base_url, "model": "m", "api_key_env": "PROFILE_KEY"}
    models._handle_models_save_locked(handler, body)
    profile = state.cfg.model_profiles[0]
    assert profile.api_key_env == "PROFILE_KEY"
    models._handle_models_save_locked(handler, {**body, "id": profile.id, "api_key_env": ""})
    assert profile.api_key_env == ""


@pytest.mark.parametrize("cross_target", [False, True])
def test_profile_explicit_environment_ownership_survives_reload(cross_target, monkeypatch, tmp_path):
    from spark.config import activate_model_profile, load_config, require_api_key, save_config

    monkeypatch.setenv("SPARK_API_KEY", "synthetic-global")
    monkeypatch.setenv("PROFILE_KEY", "synthetic-profile")
    cfg = SparkConfig()
    cfg.active_profile_id = "first"
    target = "https://other.example/v1" if cross_target else cfg.provider.base_url
    cfg.model_profiles = [ModelProfile(id="second", base_url=target, model="m", api_key_env="PROFILE_KEY")]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    cfg = load_config(config_path=path, workdir=tmp_path)
    activate_model_profile(cfg, cfg.model_profiles[0])
    assert require_api_key(cfg) == "synthetic-profile"
    save_config(cfg, path)
    cfg = load_config(config_path=path, workdir=tmp_path)
    assert cfg.model_profiles[0].api_key_env == "PROFILE_KEY"
    assert require_api_key(cfg) == "synthetic-profile"
    monkeypatch.delenv("PROFILE_KEY")
    from spark.errors import ConfigError

    with pytest.raises(ConfigError):
        require_api_key(cfg)


@pytest.mark.parametrize("explicit_project_path", [False, True])
def test_project_cannot_enable_embedding_opt_in(explicit_project_path, monkeypatch, tmp_path):
    import spark.memory.service as memory
    from spark.config import load_config

    home = tmp_path / "home"
    home.mkdir()
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("SPARK_HOME", str(home))
    (home / "config.toml").write_text('[memory]\nembedding_enabled = false\n', encoding="utf-8")
    project = workdir / ".spark.toml"
    project.write_text('[memory]\nembedding_enabled = true\n', encoding="utf-8")
    cfg = load_config(config_path=project if explicit_project_path else None, workdir=workdir)
    calls = []

    async def fake_embed(**kwargs):
        calls.append(kwargs)
        return [[1.0]]

    monkeypatch.setattr(memory, "embed_texts", fake_embed)
    assert MemoryService(None, cfg)._embed(["private text"]) is None
    assert not calls


def test_default_profile_environment_credential_round_trip(monkeypatch, tmp_path):
    import threading

    import spark.web.state as state_module
    from spark.config import activate_model_profile, load_config, require_api_key, save_config

    monkeypatch.setenv("PROFILE_KEY", "synthetic-profile")
    cfg = SparkConfig()
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "PROFILE_KEY"
    cfg.provider.api_keys = ["synthetic-pool"]
    cfg.provider.fallback_models = ["fallback"]
    cfg.provider.small_model = "small"
    path = tmp_path / "config.toml"
    monkeypatch.setattr(state_module, "save_config", lambda cfg: save_config(cfg, path))
    state = SimpleNamespace(cfg=cfg, settings_lock=threading.RLock())
    state_module.SparkWebState.ensure_default_profile(state)
    default_id = cfg.active_profile_id
    cfg.model_profiles.append(ModelProfile(id="other", base_url=cfg.provider.base_url, model="other"))
    activate_model_profile(cfg, cfg.model_profiles[1])
    save_config(cfg, path)
    cfg = load_config(config_path=path, workdir=tmp_path)
    activate_model_profile(cfg, next(p for p in cfg.model_profiles if p.id == default_id))
    assert require_api_key(cfg) == "synthetic-profile"
    assert cfg.provider.api_keys == ["synthetic-pool"]
    assert cfg.provider.fallback_models == ["fallback"]
    assert cfg.provider.small_model == "small"
    save_config(cfg, path)
    reloaded = load_config(config_path=path, workdir=tmp_path)
    assert require_api_key(reloaded) == "synthetic-profile"
    assert reloaded.provider.api_keys == ["synthetic-pool"]
    assert reloaded.provider.fallback_models == ["fallback"]
    assert reloaded.provider.small_model == "small"


def test_explicit_environment_unbind_survives_reload(models_context, monkeypatch, tmp_path):
    from spark.config import load_config, require_api_key, save_config
    from spark.errors import ConfigError

    state, handler = models_context
    monkeypatch.setenv("PROFILE_KEY", "synthetic-profile")
    state.cfg.provider.api_key = None
    state.cfg.provider.api_key_env = "PROFILE_KEY"
    state.cfg.active_profile_id = "p"
    state.cfg.model_profiles = [ModelProfile(id="p", base_url=state.cfg.provider.base_url, model="m", api_key_env="PROFILE_KEY")]
    path = tmp_path / "config.toml"
    monkeypatch.setattr(models, "_save_config", lambda cfg: save_config(cfg, path))
    models._handle_models_save_locked(handler, {"id": "p", "base_url": state.cfg.provider.base_url, "model": "m", "api_key_env": ""})
    state.cfg = load_config(config_path=path, workdir=tmp_path)
    models._handle_models_activate_locked(handler, {"id": "p"})
    assert state.cfg.provider.api_key_env == ""
    with pytest.raises(ConfigError):
        require_api_key(load_config(config_path=path, workdir=tmp_path))


def test_legacy_profile_environment_compatibility_and_identity_isolation(monkeypatch, tmp_path):
    from spark.config import activate_model_profile, load_config, require_api_key, save_config
    from spark.errors import ConfigError

    monkeypatch.setenv("PROFILE_KEY", "synthetic-profile")
    cfg = SparkConfig()
    cfg.provider.api_key_env = "PROFILE_KEY"
    cfg.active_profile_id = "legacy"
    cfg.model_profiles = [
        ModelProfile(id="legacy", base_url=cfg.provider.base_url, model="m"),
        ModelProfile(id="other", base_url=cfg.provider.base_url, model="m2"),
    ]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    cfg = load_config(config_path=path, workdir=tmp_path)
    activate_model_profile(cfg, cfg.model_profiles[0])
    assert require_api_key(cfg) == "synthetic-profile"
    activate_model_profile(cfg, cfg.model_profiles[1])
    save_config(cfg, path)
    cfg = load_config(config_path=path, workdir=tmp_path)
    with pytest.raises(ConfigError):
        require_api_key(cfg)


def test_cross_target_activation_does_not_fallback_to_old_environment_key(models_context, monkeypatch, tmp_path):
    from spark.config import load_config, require_api_key, save_config
    from spark.errors import ConfigError

    state, handler = models_context
    monkeypatch.setenv("SPARK_API_KEY", "synthetic-environment")
    state.cfg.model_profiles = [ModelProfile(id="p", base_url="https://other.example/v1", model="m")]
    models._handle_models_activate_locked(handler, {"id": "p"})
    with pytest.raises(ConfigError):
        require_api_key(state.cfg)
    path = tmp_path / "config.toml"
    save_config(state.cfg, path)
    reloaded = load_config(config_path=path, workdir=tmp_path)
    with pytest.raises(ConfigError):
        require_api_key(reloaded)

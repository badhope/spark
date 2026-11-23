
import pytest

from spark.inline_edit import generate_inline_edit
from spark.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_inline_edit_strips_fence() -> None:
    provider = MockProvider(rounds=[[__import__("spark.models", fromlist=["ChatDelta"]).ChatDelta(type="text", text="```python\nreturn x + 1\n```"), __import__("spark.models", fromlist=["ChatDelta"]).ChatDelta(type="end")]])
    result = await generate_inline_edit(
        provider,
        {"mode": "edit", "path": "a.py", "language": "python", "selected": "return x", "instruction": "add one"},
    )
    assert result["ok"] is True
    assert result["text"] == "return x + 1"


@pytest.mark.asyncio
async def test_edit_prompt_carries_surrounding_context() -> None:
    from spark.models import ChatDelta

    class Capture:
        messages = []

        async def stream(self, messages, tools, *, effort="low"):
            self.messages = messages
            yield ChatDelta(type="text", text="return x + 1")
            yield ChatDelta(type="end")

    provider = Capture()
    result = await generate_inline_edit(
        provider,
        {
            "mode": "edit",
            "path": "a.py",
            "language": "python",
            "selected": "return x",
            "prefix": "def f(x):\n    ",
            "suffix": "\n\ndef g():\n    return 2",
            "instruction": "add one",
        },
    )
    assert result["text"] == "return x + 1"
    prompt = provider.messages[-1].content
    assert "def f(x)" in prompt and "def g()" in prompt
    assert "<before_selection>" in prompt and "<after_selection>" in prompt


@pytest.mark.asyncio
async def test_nes_empty_is_not_a_failure() -> None:
    provider = MockProvider(rounds=[[__import__("spark.models", fromlist=["ChatDelta"]).ChatDelta(type="end")]])
    result = await generate_inline_edit(provider, {"mode": "nes", "path": "a.py", "prefix": "x = 1", "suffix": ""})
    assert result["ok"] is True
    assert result["text"] == ""


def test_inline_edit_apply_requires_exact_selection(tmp_path) -> None:
    from types import SimpleNamespace

    from spark.config import SparkConfig
    from spark.sandbox import WorkdirSandbox
    from spark.web._handlers_workbench import handle_inline_edit_apply

    cfg = SparkConfig()
    cfg.agent.sandbox_mode = "workspace"
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n", encoding="utf-8")
    original_hash = __import__("hashlib").sha256(target.read_bytes()).hexdigest()
    state = SimpleNamespace(
        cfg=cfg,
        workdir=tmp_path,
        sandbox=WorkdirSandbox(tmp_path, cfg),
    )
    handler = SimpleNamespace(
        _is_loopback=lambda: True,
        server=SimpleNamespace(state=state),
        _json=lambda status, payload: handler.responses.append((status, payload)),
        responses=[],
    )
    handle_inline_edit_apply(
        handler,
        {"path": "a.py", "selected": "x = 1", "replacement": "x = 10"},
    )
    status, payload = handler.responses[-1]
    assert status == 200 and payload["changed"] is True
    assert target.read_text(encoding="utf-8") == "x = 10\ny = 2\n"

    handle_inline_edit_apply(
        handler,
        {
            "path": "a.py",
            "selected": "y = 2",
            "replacement": "y = 20",
            "base_sha256": original_hash,
        },
    )
    status, payload = handler.responses[-1]
    assert status == 409
    assert payload["stale"] is True

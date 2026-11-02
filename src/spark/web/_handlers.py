"""Standalone POST route handlers — facade over domain submodules.

历史：本文件曾是单个 862 行文件；为可维护性按域拆分为：

* ``_handlers_common.py``    — 共享助手（``_save_config``）
* ``_handlers_models.py``    — 模型档案 CRUD + 一键切换
* ``_handlers_knowledge.py`` — AGENTS.md 编辑 + 长期记忆管理
* ``_handlers_sessions.py``  — 取消/审批/会话生命周期/检查点回滚
* ``_handlers_chat.py``      — 对话（含 SSE 流式与 /test slash 回灌）
* ``_handlers_settings.py``  — 设置保存 + 模型探活

子模块是真实实现位置；本模块保留原导入路径——``routes.py`` 的 dispatch
表与测试从 ``spark.web._handlers`` 取符号，re-export 保持全部稳定。
"""

from __future__ import annotations

from spark.web._handlers_chat import (  # noqa: F401  (re-export for backward-compat)
    _handle_chat,
    _handle_chat_stream,
    _handle_shell,
)
from spark.web._handlers_common import (  # noqa: F401
    _save_config,
)
from spark.web._handlers_content import (  # noqa: F401
    _handle_knowledge,
    _handle_plugins,
    _handle_prompts,
    _handle_skills,
)
from spark.web._handlers_governance import (  # noqa: F401
    _handle_approval_profiles_activate,
    _handle_approval_profiles_delete,
    _handle_approval_profiles_save,
    _handle_permissions_rules,
    _handle_permissions_rules_delete,
)
from spark.web._handlers_health import (  # noqa: F401
    _handle_diagnostics,
    _handle_selfheal,
)
from spark.web._handlers_knowledge import (  # noqa: F401
    _handle_agents_md,
    _handle_memory,
)
from spark.web._handlers_models import (  # noqa: F401
    _handle_models_activate,
    _handle_models_delete,
    _handle_models_save,
)
from spark.web._handlers_sessions import (  # noqa: F401
    _handle_approval,
    _handle_cancel,
    _handle_checkpoints_rollback,
    _handle_messages_delete,
    _handle_messages_truncate,
    _handle_question_answer,
    _handle_session_export,
    _handle_session_import,
    _handle_sessions_delete,
    _handle_sessions_fork,
    _handle_sessions_new,
    _handle_sessions_rename,
    _handle_sessions_switch,
    _history_messages,
)
from spark.web._handlers_settings import (  # noqa: F401
    _handle_settings,
    _handle_test,
)

__all__ = [
    "_handle_agents_md",
    "_handle_approval",
    "_handle_approval_profiles_activate",
    "_handle_approval_profiles_delete",
    "_handle_approval_profiles_save",
    "_handle_cancel",
    "_handle_chat",
    "_handle_chat_stream",
    "_handle_checkpoints_rollback",
    "_handle_diagnostics",
    "_handle_memory",
    "_handle_messages_delete",
    "_handle_messages_truncate",
    "_handle_models_activate",
    "_handle_models_delete",
    "_handle_models_save",
    "_handle_permissions_rules",
    "_handle_permissions_rules_delete",
    "_handle_selfheal",
    "_handle_question_answer",
    "_handle_sessions_delete",
    "_handle_session_export",
    "_handle_session_import",
    "_handle_sessions_fork",
    "_handle_sessions_new",
    "_handle_sessions_rename",
    "_handle_sessions_switch",
    "_handle_settings",
    "_handle_shell",
    "_handle_test",
    "_history_messages",
    "_save_config",
]

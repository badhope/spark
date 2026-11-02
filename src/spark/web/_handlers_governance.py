"""权限规则与审批档案的写接口（H3/H4 治理面）。

历史：这两个配置段此前只能手改 ``config.toml``。``/api/config`` 会把它们
回显给 UI，但 ``/api/settings`` 明确拒绝写入（半成品写路径比不支持更危险）。
本模块补上**受控**的写路径，并按 ``docs/API.md`` 的设计实现：

* 权限规则只允许写 ``user`` 层。``project`` / ``local`` 层由工作目录里的
  ``.spark/settings.toml`` 装配，仓库自带的那份是**不可信输入**——给它开写
  接口等于让克隆下来的仓库给自己授权。``user`` 层规则改动写进全局配置。
* 审批档案激活会改写审批模式、工具白名单与 system prompt，因此必须重建
  ``AgentLoop``，且与 ``/api/settings`` 一样串行化（``settings_lock`` →
  非阻塞 ``turn_lock``），避免和正在跑的 turn 抢状态。
* 所有写操作走审计流（``spark.audit``），因为它们是安全边界的变更。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from spark.config import PermissionRule
from spark.errors import SparkError
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

logger = logging.getLogger(__name__)

_PERMISSION_ACTIONS = {"allow", "prompt", "deny"}
_WRITABLE_SCOPES = {"user"}


def _audit(kind: str, **fields: Any) -> None:
    """写审计流；失败绝不影响请求（与 hooks 同一纪律）。"""
    try:
        from spark import audit

        audit.record(kind, **fields)
    except Exception:  # noqa: BLE001 - 审计不得打断请求
        logger.debug("audit write failed", exc_info=True)


def _parse_rules(raw: Any) -> tuple[list[PermissionRule], str | None]:
    """把请求体里的规则列表解析成模型；非法项返回错误而不是静默丢弃。

    静默丢弃在这里尤其危险：用户以为加了一条 ``deny``，实际没生效。
    """
    if not isinstance(raw, list):
        return [], "rules must be an array"
    rules: list[PermissionRule] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"rules[{index}] must be an object"
        action = str(item.get("action") or "").strip()
        resource = str(item.get("resource") or "").strip()
        if action not in _PERMISSION_ACTIONS:
            return [], f"rules[{index}].action must be allow, prompt or deny"
        if not resource:
            return [], f"rules[{index}].resource is required"
        rules.append(
            PermissionRule(
                action=cast(Any, action),
                resource=resource,
                tool=str(item.get("tool") or "").strip(),
            )
        )
    return rules, None


def _with_settings_guard(handler: HandlerBase, body: dict, inner) -> None:
    """统一守卫：settings_lock + 非阻塞 turn_lock，与 /api/settings 同序。"""
    state = _route_state(handler)
    with state.settings_lock:
        if not state.turn_lock.acquire(blocking=False):
            handler._json(409, {"error": "agent is busy"})
            return
        try:
            inner(state, body)
        finally:
            state.turn_lock.release()


def _persist(state) -> None:
    """写盘并把新配置推给运行中的 loop（沙箱可能随规则变化）。"""
    _save_config(state.cfg)
    from spark.sandbox import WorkdirSandbox

    state.sandbox = WorkdirSandbox(state.workdir, state.cfg)


# ---------------------------------------------------------------------------
# 权限规则
# ---------------------------------------------------------------------------


def _handle_permissions_rules(handler: HandlerBase, body: dict) -> None:
    """整体替换某一作用域的权限规则（目前只允许 ``user`` 层）。"""

    def _run(state, body: dict) -> None:
        scope = str(body.get("scope") or "user").strip()
        if scope not in _WRITABLE_SCOPES:
            handler._json(
                400,
                {
                    "error": (
                        f"scope '{scope}' is not writable over the API; "
                        "project/local rules come from files in the workdir"
                    )
                },
            )
            return
        rules, error = _parse_rules(body.get("rules"))
        if error:
            handler._json(400, {"error": error})
            return
        before = list(state.cfg.permission_rules)
        state.cfg.permission_rules = rules
        try:
            # 规则会改变 decide() 的结果，必须整体复核并让沙箱看到新配置。
            from spark.web._handlers_settings import _validate_candidate

            candidate_error = _validate_candidate(state.cfg)
            if candidate_error:
                state.cfg.permission_rules = before
                handler._json(400, {"error": candidate_error})
                return
            _persist(state)
        except Exception as exc:  # aqg: top-level boundary
            state.cfg.permission_rules = before
            handler._json(500, {"error": f"failed to save permission rules: {exc}"})
            return
        _audit(
            "permission_rules_written",
            scope=scope,
            count=len(rules),
            actions=[rule.action for rule in rules],
        )
        handler._json(
            200,
            {
                "ok": True,
                "scope": scope,
                "permission_rules": [
                    {"action": r.action, "resource": r.resource, "tool": r.tool}
                    for r in rules
                ],
            },
        )

    _with_settings_guard(handler, body, _run)


def _handle_permissions_rules_delete(handler: HandlerBase, body: dict) -> None:
    """按序号删除一条 user 层规则。

    用序号而不是 glob 字符串作主键：``**/*.py`` 这类模式不能当稳定 id。
    """

    def _run(state, body: dict) -> None:
        scope = str(body.get("scope") or "user").strip()
        if scope not in _WRITABLE_SCOPES:
            handler._json(400, {"error": f"scope '{scope}' is not writable over the API"})
            return
        index = body.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            handler._json(400, {"error": "index (non-negative int) required"})
            return
        rules = list(state.cfg.permission_rules)
        if index >= len(rules):
            handler._json(404, {"error": f"rule index out of range (0..{len(rules) - 1})"})
            return
        removed = rules.pop(index)
        state.cfg.permission_rules = rules
        try:
            _persist(state)
        except Exception as exc:  # aqg: top-level boundary
            state.cfg.permission_rules = rules[:index] + [removed] + rules[index:]
            handler._json(500, {"error": f"failed to save permission rules: {exc}"})
            return
        _audit(
            "permission_rules_written",
            scope=scope,
            count=len(rules),
            removed=f"{removed.action}:{removed.resource}",
        )
        handler._json(200, {"ok": True, "removed": index, "remaining": len(rules)})

    _with_settings_guard(handler, body, _run)


# ---------------------------------------------------------------------------
# 审批档案
# ---------------------------------------------------------------------------


def _handle_approval_profiles_save(handler: HandlerBase, body: dict) -> None:
    """新建或更新一个审批档案（无 ``id`` 为新建，与 /api/models/save 同构）。"""

    def _run(state, body: dict) -> None:
        from spark.config import APPROVAL_MODES, ApprovalProfile

        name = str(body.get("name") or "").strip()
        approval = str(body.get("approval") or "").strip()
        if not name:
            handler._json(400, {"error": "name is required"})
            return
        if approval and approval not in APPROVAL_MODES:
            handler._json(400, {"error": f"approval must be one of {', '.join(APPROVAL_MODES)}"})
            return
        raw_tools = body.get("tools")
        tools: list[str] = []
        if raw_tools is not None:
            if not isinstance(raw_tools, list):
                handler._json(400, {"error": "tools must be an array of globs"})
                return
            tools = [str(t).strip() for t in raw_tools if str(t).strip()]
        model_profile_id = str(body.get("model_profile_id") or "").strip()
        if model_profile_id and not any(
            p.id == model_profile_id for p in state.cfg.model_profiles
        ):
            handler._json(404, {"error": f"unknown model profile: {model_profile_id}"})
            return

        pid = str(body.get("id") or "").strip()
        existing = next(
            (p for p in state.cfg.approval_profiles if p.id == pid), None
        )
        if existing is not None:
            existing.name = name
            existing.approval = cast(Any, approval or existing.approval)
            existing.tools = tools
            existing.system_prompt = str(body.get("system_prompt") or "").strip()
            existing.model_profile_id = model_profile_id
            profile = existing
        else:
            if not approval:
                handler._json(400, {"error": "approval is required when creating a profile"})
                return
            profile = ApprovalProfile(
                id=uuid.uuid4().hex[:8],
                name=name,
                approval=cast(Any, approval),
                tools=tools,
                system_prompt=str(body.get("system_prompt") or "").strip(),
                model_profile_id=model_profile_id,
            )
            state.cfg.approval_profiles = [*state.cfg.approval_profiles, profile]
        try:
            _persist(state)
        except Exception as exc:  # aqg: top-level boundary
            handler._json(500, {"error": f"failed to save approval profile: {exc}"})
            return
        _audit("approval_profile_written", id=profile.id, name=profile.name, approval=profile.approval)
        handler._json(200, {"ok": True, "id": profile.id})

    _with_settings_guard(handler, body, _run)


def _handle_approval_profiles_delete(handler: HandlerBase, body: dict) -> None:
    def _run(state, body: dict) -> None:
        pid = str(body.get("id") or "").strip()
        before = len(state.cfg.approval_profiles)
        state.cfg.approval_profiles = [
            p for p in state.cfg.approval_profiles if p.id != pid
        ]
        if len(state.cfg.approval_profiles) == before:
            handler._json(404, {"error": "unknown approval profile"})
            return
        # 删除活跃档案时必须清空指针，否则 _apply_approval_profile 会静默失效。
        if state.cfg.active_approval_profile_id == pid:
            state.cfg.active_approval_profile_id = ""
        try:
            _persist(state)
        except Exception as exc:  # aqg: top-level boundary
            handler._json(500, {"error": f"failed to delete approval profile: {exc}"})
            return
        _audit("approval_profile_deleted", id=pid)
        handler._json(200, {"ok": True})

    _with_settings_guard(handler, body, _run)


def _handle_approval_profiles_activate(handler: HandlerBase, body: dict) -> None:
    """激活档案并重建 loop（审批模式/工具白名单/system prompt 立即生效）。"""

    def _run(state, body: dict) -> None:
        if state.busy:
            handler._json(409, {"error": "agent is busy"})
            return
        pid = str(body.get("id") or "").strip()
        profile = next(
            (p for p in state.cfg.approval_profiles if p.id == pid), None
        )
        if profile is None:
            handler._json(404, {"error": "unknown approval profile"})
            return
        old_id = state.cfg.active_approval_profile_id
        state.cfg.active_approval_profile_id = profile.id
        try:
            # 档案可联动模型档案：与 `spark profile use` 同一路径。
            if profile.model_profile_id:
                model_profile = next(
                    (m for m in state.cfg.model_profiles if m.id == profile.model_profile_id),
                    None,
                )
                if model_profile is not None:
                    from spark.config import activate_model_profile

                    activate_model_profile(state.cfg, model_profile)
            state.rebuild_loop(session_id=state.session_id)
            _persist(state)
        except SparkError as exc:
            state.cfg.active_approval_profile_id = old_id
            handler._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # aqg: top-level boundary
            state.cfg.active_approval_profile_id = old_id
            handler._json(500, {"error": f"failed to activate approval profile: {exc}"})
            return
        _audit("approval_profile_activated", id=profile.id, approval=profile.approval)
        handler._json(
            200,
            {
                "ok": True,
                "status": state.status(),
                "active_approval_profile_id": profile.id,
            },
        )

    _with_settings_guard(handler, body, _run)

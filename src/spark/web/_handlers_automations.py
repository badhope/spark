"""定时任务的 HTTP 接口。

与治理面同一套纪律：写操作串行化、非法输入显式报错、成功即审计。
执行不在这里发生——调度器只负责"到点了"，真正的回合由调用方在
持有运行时的线程里跑，避免把审批与 SSE 拖进后台线程。
"""

from __future__ import annotations

import logging

from spark.automations import CronError, looks_like_cron, new_automation, parse_cron
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

logger = logging.getLogger(__name__)


def _handle_automations(handler: HandlerBase, body: dict) -> None:
    """action: list / create / update / delete / toggle / run_now"""
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()

    if action == "list":
        handler._json(200, {"automations": state.store.list_automations()})
        return

    if action == "create":
        try:
            record = new_automation(
                name=str(body.get("name") or ""),
                prompt=str(body.get("prompt") or ""),
                cron=str(body.get("cron") or ""),
                enabled=bool(body.get("enabled", True)),
                workdir=str(body.get("workdir") or state.workdir),
            )
        except CronError as exc:
            handler._json(400, {"error": str(exc)})
            return
        state.store.upsert_automation(record)
        handler._json(200, {"ok": True, "automation": record})
        return

    if action in {"update", "toggle", "delete", "run_now"}:
        aid = str(body.get("id") or "").strip()
        existing = state.store.get_automation(aid) if aid else None
        if existing is None:
            handler._json(404, {"error": "unknown automation"})
            return

        if action == "delete":
            state.store.delete_automation(aid)
            handler._json(200, {"ok": True})
            return

        if action == "toggle":
            existing["enabled"] = not bool(existing.get("enabled"))
            if existing["enabled"]:
                # 重新启用时按当前时间重算下一次，避免补跑关闭期间积压的轮次。
                from datetime import datetime

                from spark.automations import next_run_after

                existing["next_run_at"] = next_run_after(
                    existing["cron"], datetime.now()
                ).timestamp()
            state.store.upsert_automation(existing)
            handler._json(200, {"ok": True, "automation": existing})
            return

        if action == "update":
            name = str(body.get("name") or existing["name"]).strip()
            prompt = str(body.get("prompt") or existing["prompt"]).strip()
            cron = str(body.get("cron") or existing["cron"]).strip()
            if not name or not prompt:
                handler._json(400, {"error": "name and prompt are required"})
                return
            try:
                parse_cron(cron)
            except CronError as exc:
                handler._json(400, {"error": str(exc)})
                return
            from datetime import datetime

            from spark.automations import next_run_after

            existing.update(
                name=name,
                prompt=prompt,
                cron=cron,
                next_run_at=next_run_after(cron, datetime.now()).timestamp(),
            )
            state.store.upsert_automation(existing)
            handler._json(200, {"ok": True, "automation": existing})
            return

        # run_now：把 next_run_at 拨到过去，交给调度器下一拍取走。
        # 不在请求线程里直接跑 turn——那会让一个长任务卡住整个 HTTP 响应。
        existing["next_run_at"] = 0
        state.store.upsert_automation(existing)
        handler._json(
            200,
            {"ok": True, "queued": True, "hint": "the scheduler picks it up within 30s"},
        )
        return

    handler._json(400, {"error": "action must be list, create, update, delete, toggle or run_now"})


def _handle_automations_cron_check(handler: HandlerBase, body: dict) -> None:
    """给前端做实时校验：表达式是否合法、下一次何时跑。"""
    from datetime import datetime

    from spark.automations import next_run_after

    expr = str(body.get("cron") or "").strip()
    if not looks_like_cron(expr):
        handler._json(200, {"valid": False, "error": "invalid cron expression"})
        return
    try:
        nxt = next_run_after(expr, datetime.now())
    except CronError as exc:
        handler._json(200, {"valid": False, "error": str(exc)})
        return
    handler._json(
        200,
        {"valid": True, "next_run_at": nxt.timestamp(), "next_run_iso": nxt.isoformat()},
    )

"""定时任务（Automations）——对应 ZCode Automations / Qoder `/crontab`。

定位取舍：本项目是**本地单进程 agent**，不是常驻云服务。因此这里的调度器
刻意做成「进程内 + 显式触发」两条路径：

* 进程活着时，一个轻量后台线程按 cron 表达式到点就绪；
* 进程没活着时，任务不会丢——`due()` 按 `last_run` 计算是否已过期，
  下次启动（或 `spark automations run-due`）立刻补跑一次。

语义刻意保守：

* **不并发**。同一任务永远串行；上一轮没跑完就跳过本轮并记录 skipped，
  而不是堆叠出一堆并行 turn（那是烧钱与互相踩文件的最短路径）。
* **只跑 prompt，不自动审批**。调度触发的回合走用户配置的审批档；如果
  档位需要人工确认而无人值守，该轮会停在审批上并标记 waiting，而不是
  悄悄放行写操作。
* **产物落在会话里**。每次运行是一个普通 session，可回看、可回放，
  不做第二套日志。
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: 支持的最小 cron 子集：`分 时 日 月 周`，每段支持 `*`、`*/n`、`a,b,c`、`a-b`。
#: 刻意不引入 croniter 之类的依赖——这个项目已有的原则是不为新能力加依赖，
#: 而且完整 cron 语法（@reboot、L/W/# 等）在本地 agent 场景用不上。
_CRON_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),
)

MIN_INTERVAL_SEC = 60


class CronError(ValueError):
    """cron 表达式无法解析。"""


def _expand_field(raw: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty field component in {raw!r}")
        step = 1
        if "/" in part:
            part, _, step_raw = part.partition("/")
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise CronError(f"invalid step in {raw!r}") from exc
            if step <= 0:
                raise CronError(f"step must be positive in {raw!r}")
        if part in {"*", ""}:
            start, end = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError as exc:
                raise CronError(f"invalid range in {raw!r}") from exc
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise CronError(f"invalid value {part!r} in {raw!r}") from exc
        if start < low or end > high or start > end:
            raise CronError(f"{raw!r} out of range {low}-{high}")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> dict[str, set[int]]:
    """解析 5 段 cron；非法时抛 :class:`CronError`（含可读原因）。"""
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(
            f"cron must have 5 fields (minute hour day month weekday), got {len(fields)}"
        )
    parsed: dict[str, set[int]] = {}
    for (name, low, high), raw in zip(_CRON_FIELDS, fields):
        parsed[name] = _expand_field(raw, low, high)
    return parsed


def next_run_after(expr: str, after: datetime) -> datetime:
    """返回 *after* 之后第一个匹配的时间（精确到分钟）。

    逐分钟向前试探，最多 366 天；找不到就抛错，而不是死循环。
    """
    spec = parse_cron(expr)
    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = moment + timedelta(days=366)
    while moment < limit:
        # cron 的 weekday: 0=周日；Python 的 weekday(): 0=周一
        weekday = (moment.weekday() + 1) % 7
        if (
            moment.minute in spec["minute"]
            and moment.hour in spec["hour"]
            and moment.day in spec["day"]
            and moment.month in spec["month"]
            and weekday in spec["weekday"]
        ):
            return moment
        moment += timedelta(minutes=1)
    raise CronError(f"no run time found within a year for {expr!r}")


def normalize_interval(minutes: int) -> str:
    """把「每 N 分钟」转成 cron；N 必须能被 60 整除才表达成 `*/N`。"""
    if minutes < 1:
        raise CronError("interval must be at least 1 minute")
    if minutes < 60:
        return f"*/{minutes} * * * *"
    hours, rest = divmod(minutes, 60)
    if rest:
        raise CronError("intervals above 60 minutes must be whole hours")
    if hours > 23:
        raise CronError("intervals above 24 hours must use a daily cron expression")
    return f"0 */{hours} * * *" if hours > 1 else "0 * * * *"


def _now() -> float:
    return time.time()


def _is_closed_db(exc: BaseException) -> bool:
    """识别"数据库已关闭"——宿主停机时的正常信号，不该当异常刷日志。"""
    text = str(exc).lower()
    return "closed database" in text or "cannot operate on a closed" in text


class AutomationScheduler:
    """进程内的轻量调度器：到点把任务标记为 due，由调用方真正执行。

    刻意不在线程里直接跑 AgentLoop：那会把审批、SSE、会话状态全部拖进
    后台线程，出错时极难复现。这里只负责"什么时候该跑"，执行交给持有
    运行时的调用方（Web 的 state / CLI）。
    """

    def __init__(self, store, *, tick_sec: float = 30.0, on_due=None) -> None:
        self._store = store
        self._tick = tick_sec
        self._on_due = on_due
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="spark-automations", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            if self._stop.is_set():
                break
            try:
                if self._on_due is not None:
                    # 回调自己负责取锁与执行；调度器只负责"到点了"。
                    self._on_due()
                else:
                    self.run_due()
            except Exception as exc:  # aqg: top-level boundary — 调度线程绝不能死
                # store 被关掉（宿主停机）是正常收尾，不是错误：安静退出，
                # 免得日志里刷满 "Cannot operate on a closed database"。
                if _is_closed_db(exc):
                    logger.debug("automation store closed; scheduler stopping")
                    self._stop.set()
                    break
                logger.warning("automation tick failed", exc_info=True)

    def run_due(self) -> list[dict]:
        """把所有已到期任务标记为 due 并返回它们（不执行）。

        到点即写 `last_run`，保证同一时刻只被判为 due 一次；即使执行失败
        也不会在同一分钟内被反复触发。
        """
        if self._stop.is_set():
            return []
        now = _now()
        due: list[dict] = []
        try:
            rows = self._store.list_automations()
        except Exception:
            # store 已关闭（服务停机中）：安静退出，不要刷一屏 traceback。
            logger.debug("automation store unavailable; skipping tick")
            return []
        for row in rows:
            if not row.get("enabled"):
                continue
            next_at = row.get("next_run_at") or 0
            if next_at and next_at > now:
                continue
            if not next_at:
                # 新建任务还没有 next_run_at：按 cron 补算，不立刻执行，
                # 避免"刚建就炸一次"。
                self._store.update_automation_schedule(
                    row["id"], last_run=row.get("last_run") or now,
                    next_run_at=next_run_after(row["cron"], datetime.now()).timestamp(),
                )
                continue
            try:
                upcoming = next_run_after(row["cron"], datetime.now()).timestamp()
            except CronError:
                logger.warning("automation %s has invalid cron", row["id"])
                continue
            self._store.update_automation_schedule(
                row["id"], last_run=now, next_run_at=upcoming
            )
            due.append({**row, "due_at": next_at})
        return due


def new_automation(
    *,
    name: str,
    prompt: str,
    cron: str,
    enabled: bool = True,
    workdir: str = "",
) -> dict[str, Any]:
    """构造一条自动化记录；cron 在入库前必须校验通过。"""
    if not name.strip():
        raise CronError("name is required")
    if not prompt.strip():
        raise CronError("prompt is required")
    parse_cron(cron)
    now = _now()
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip(),
        "prompt": prompt.strip(),
        "cron": cron.strip(),
        "enabled": bool(enabled),
        "workdir": workdir,
        "created_at": now,
        "last_run": 0.0,
        "next_run_at": next_run_after(cron, datetime.now()).timestamp(),
        "last_status": "",
        "last_session_id": "",
        "last_error": "",
        "skipped_count": 0,
    }


_CRON_RE = re.compile(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$")


def looks_like_cron(value: str) -> bool:
    """快速判定（用于 UI 提示），真正校验仍走 :func:`parse_cron`。"""
    if not _CRON_RE.match(value or ""):
        return False
    try:
        parse_cron(value)
        return True
    except CronError:
        return False

"""YAML workflow specs and cross-process path locks for multi-agent runs.

This is the declaration layer only: roles, steps, dependencies and write scopes.
Execution lives in the agent loop; keeping validation separate means a broken
workflow fails before any model/tool call and can be checked in CI.
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml
from filelock import FileLock, Timeout

from spark.errors import SparkError

STEP_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{0,63}$")
ROLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{0,63}$")
LOCK_DIR = Path(".spark") / "locks"


class WorkflowError(SparkError):
    """A workflow declaration or lock operation is invalid."""


@dataclass(frozen=True)
class RoleSpec:
    name: str
    agent: str = ""
    description: str = ""
    model: str = ""
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepSpec:
    id: str
    role: str
    prompt: str
    needs: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    max_attempts: int = 1


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: int
    description: str
    roles: dict[str, RoleSpec]
    steps: tuple[StepSpec, ...] = field(default_factory=tuple)

    @property
    def by_id(self) -> dict[str, StepSpec]:
        return {step.id: step for step in self.steps}

    def order(self) -> list[str]:
        """Deterministic topological order; raises on an invalid graph."""
        return _topological_order(self.steps)

    def ready(self, completed: set[str]) -> list[StepSpec]:
        """Steps whose dependencies are all completed, in declaration order."""
        by_id = self.by_id
        out: list[StepSpec] = []
        for step in self.steps:
            if step.id in completed:
                continue
            if all(dep in completed or dep not in by_id for dep in step.needs):
                out.append(step)
        return out

    def graph(self, statuses: dict[str, str] | None = None) -> dict[str, Any]:
        """Stable node/edge shape for the execution-graph UI."""
        statuses = statuses or {}
        nodes = [
            {
                "id": step.id,
                "role": step.role,
                "agent": self.roles[step.role].agent,
                "prompt": step.prompt[:160],
                "status": statuses.get(step.id, "pending"),
            }
            for step in self.steps
        ]
        edges = [
            {"from": dep, "to": step.id}
            for step in self.steps
            for dep in step.needs
        ]
        return {"name": self.name, "nodes": nodes, "edges": edges}


def _topological_order(steps: tuple[StepSpec, ...]) -> list[str]:
    by_id = {step.id: step for step in steps}
    indegree = {step.id: len(step.needs) for step in steps}
    children: dict[str, list[str]] = {step.id: [] for step in steps}
    for step in steps:
        for dep in step.needs:
            children[dep].append(step.id)
    queue = deque(step.id for step in steps if indegree[step.id] == 0)
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(by_id):
        raise WorkflowError("工作流存在循环依赖")
    return order


def _as_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise WorkflowError(f"{field_name} 必须是字符串列表")
        return tuple(item.strip() for item in value if item.strip())
    raise WorkflowError(f"{field_name} 必须是字符串或字符串列表")


def _safe_write_glob(pattern: str) -> str:
    raw = pattern.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw:
        raise WorkflowError(f"write 路径必须是工作区相对路径: {pattern}")
    if any(part == ".." for part in raw.split("/")):
        raise WorkflowError(f"write 路径不能越过工作区: {pattern}")
    return raw


def load_workflow(
    path: Path,
    *,
    workdir: Path | None = None,
    available_agents: set[str] | None = None,
) -> WorkflowSpec:
    """Load and validate a YAML workflow file."""
    target = path.expanduser()
    if not target.is_absolute() and workdir is not None:
        target = workdir / target
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"无法读取工作流: {target}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"工作流 YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError("工作流必须是 YAML 对象")
    name = str(data.get("name") or target.stem).strip()
    if not name:
        raise WorkflowError("工作流 name 不能为空")
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise WorkflowError("version 必须是正整数")
    description = str(data.get("description") or "").strip()

    raw_roles = data.get("roles")
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise WorkflowError("roles 必须是非空对象")
    roles: dict[str, RoleSpec] = {}
    for role_name, raw in raw_roles.items():
        role = str(role_name)
        if not ROLE_NAME_RE.fullmatch(role):
            raise WorkflowError(f"非法 role 名: {role}")
        if isinstance(raw, str):
            roles[role] = RoleSpec(name=role, agent=raw.strip())
        elif isinstance(raw, dict):
            roles[role] = RoleSpec(
                name=role,
                agent=str(raw.get("agent") or "").strip(),
                description=str(raw.get("description") or "").strip(),
                model=str(raw.get("model") or "").strip(),
                tools=_as_tuple(raw.get("tools"), f"roles.{role}.tools"),
            )
        else:
            raise WorkflowError(f"roles.{role} 必须是字符串或对象")
        if available_agents is not None and roles[role].agent:
            if roles[role].agent not in available_agents:
                raise WorkflowError(
                    f"roles.{role}.agent 未找到声明式 agent: {roles[role].agent}"
                )

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError("steps 必须是非空数组")
    steps: list[StepSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise WorkflowError(f"steps[{index}] 必须是对象")
        step_id = str(raw.get("id") or "").strip()
        if not STEP_ID_RE.fullmatch(step_id):
            raise WorkflowError(f"steps[{index}].id 非法: {step_id!r}")
        if step_id in seen:
            raise WorkflowError(f"重复的 step id: {step_id}")
        role = str(raw.get("role") or "").strip()
        if role not in roles:
            raise WorkflowError(f"step {step_id} 引用了不存在的 role: {role}")
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            raise WorkflowError(f"step {step_id} 的 prompt 不能为空")
        needs = _as_tuple(raw.get("needs"), f"steps.{step_id}.needs")
        if step_id in needs:
            raise WorkflowError(f"step {step_id} 不能依赖自己")
        writes = tuple(
            _safe_write_glob(item)
            for item in _as_tuple(raw.get("writes"), f"steps.{step_id}.writes")
        )
        max_attempts = raw.get("max_attempts", 1)
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise WorkflowError(f"steps.{step_id}.max_attempts 必须是 1..5")
        seen.add(step_id)
        steps.append(
            StepSpec(
                id=step_id,
                role=role,
                prompt=prompt,
                needs=needs,
                writes=writes,
                max_attempts=max_attempts,
            )
        )
    by_id = {step.id: step for step in steps}
    for step in steps:
        missing = [dep for dep in step.needs if dep not in by_id]
        if missing:
            raise WorkflowError(
                f"step {step.id} 缺少依赖: {', '.join(missing)}"
            )
    _topological_order(tuple(steps))  # validates cycles before returning
    return WorkflowSpec(
        name=name,
        version=version,
        description=description,
        roles=roles,
        steps=tuple(steps),
    )


def _lock_key(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:20]


class WorkspaceLocks:
    """Per-path locks that protect two workers from writing the same file.

    Locks are acquired in sorted order, so two workers locking overlapping
    sets cannot deadlock. Only paths inside the workspace are accepted.
    """

    def __init__(self, workdir: Path, *, timeout: float = 30.0) -> None:
        self.workdir = workdir.resolve()
        self.timeout = timeout
        self.lock_dir = self.workdir / LOCK_DIR
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def _relative(self, path: str | Path) -> str:
        raw = Path(path)
        resolved = (raw if raw.is_absolute() else self.workdir / raw).resolve()
        try:
            rel = resolved.relative_to(self.workdir).as_posix()
        except ValueError as exc:
            raise WorkflowError(f"锁路径越过工作区: {path}") from exc
        return rel

    @contextmanager
    def acquire(self, paths: list[str | Path]) -> Iterator[None]:
        rels = sorted({self._relative(path) for path in paths})
        stack = ExitStack()
        try:
            for rel in rels:
                lock_path = self.lock_dir / f"{_lock_key(rel)}.lock"
                lock = FileLock(str(lock_path), timeout=self.timeout)
                try:
                    stack.enter_context(lock)
                except Timeout as exc:
                    raise WorkflowError(f"等待文件锁超时: {rel}") from exc
            yield
        finally:
            stack.close()

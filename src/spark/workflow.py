"""YAML workflow specs and cross-process path locks for multi-agent runs.

This is the declaration layer only: roles, steps, dependencies and write scopes.
Execution lives in the agent loop; keeping validation separate means a broken
workflow fails before any model/tool call and can be checked in CI.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

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


def _read_workflow_data(path: Path, workdir: Path | None) -> tuple[Path, dict[str, Any]]:
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
    return target, data


def _parse_role(
    role: str,
    raw: Any,
    available_agents: set[str] | None,
) -> RoleSpec:
    if not ROLE_NAME_RE.fullmatch(role):
        raise WorkflowError(f"非法 role 名: {role}")
    if isinstance(raw, str):
        spec = RoleSpec(name=role, agent=raw.strip())
    elif isinstance(raw, dict):
        spec = RoleSpec(
            name=role,
            agent=str(raw.get("agent") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            model=str(raw.get("model") or "").strip(),
            tools=_as_tuple(raw.get("tools"), f"roles.{role}.tools"),
        )
    else:
        raise WorkflowError(f"roles.{role} 必须是字符串或对象")
    if available_agents is not None and spec.agent and spec.agent not in available_agents:
        raise WorkflowError(f"roles.{role}.agent 未找到声明式 agent: {spec.agent}")
    return spec


def _parse_roles(raw_roles: Any, available_agents: set[str] | None) -> dict[str, RoleSpec]:
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise WorkflowError("roles 必须是非空对象")
    roles: dict[str, RoleSpec] = {}
    for role_name, raw in raw_roles.items():
        role = str(role_name)
        roles[role] = _parse_role(role, raw, available_agents)
    return roles


def _parse_step(
    raw: Any,
    index: int,
    roles: dict[str, RoleSpec],
    seen: set[str],
) -> StepSpec:
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
    return StepSpec(
        id=step_id,
        role=role,
        prompt=prompt,
        needs=needs,
        writes=writes,
        max_attempts=max_attempts,
    )


def _parse_steps(raw_steps: Any, roles: dict[str, RoleSpec]) -> tuple[StepSpec, ...]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError("steps 必须是非空数组")
    steps: list[StepSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps):
        steps.append(_parse_step(raw, index, roles, seen))
    return tuple(steps)


def _validate_dependencies(steps: tuple[StepSpec, ...]) -> None:
    by_id = {step.id: step for step in steps}
    for step in steps:
        missing = [dep for dep in step.needs if dep not in by_id]
        if missing:
            raise WorkflowError(f"step {step.id} 缺少依赖: {', '.join(missing)}")
    _topological_order(steps)  # validates cycles before returning


def load_workflow(
    path: Path,
    *,
    workdir: Path | None = None,
    available_agents: set[str] | None = None,
) -> WorkflowSpec:
    """Load and validate a YAML workflow file."""
    target, data = _read_workflow_data(path, workdir)
    name = str(data.get("name") or target.stem).strip()
    if not name:
        raise WorkflowError("工作流 name 不能为空")
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise WorkflowError("version 必须是正整数")
    description = str(data.get("description") or "").strip()
    roles = _parse_roles(data.get("roles"), available_agents)
    steps = _parse_steps(data.get("steps"), roles)
    _validate_dependencies(steps)
    return WorkflowSpec(
        name=name,
        version=version,
        description=description,
        roles=roles,
        steps=steps,
    )


def _lock_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]


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
    def acquire(self, paths: Sequence[str | Path]) -> Iterator[None]:
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


# ---------------------------------------------------------------------------
# Execution engine (no model dependency)
# ---------------------------------------------------------------------------


@dataclass
class WorkflowRun:
    spec: WorkflowSpec
    statuses: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    costs: dict[str, float] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.statuses:
            self.statuses = {step.id: "pending" for step in self.spec.steps}

    @property
    def completed(self) -> set[str]:
        return {name for name, state in self.statuses.items() if state == "completed"}

    @property
    def ok(self) -> bool:
        return all(state == "completed" for state in self.statuses.values())

    @property
    def paused(self) -> bool:
        return any(state == "paused" for state in self.statuses.values())

    @property
    def cost_usd(self) -> float:
        return round(sum(self.costs.values()), 6)

    def graph(self) -> dict[str, Any]:
        return self.spec.graph(self.statuses)

    def to_state(self) -> dict[str, Any]:
        """Serializable run state, enough to resume in a later process."""
        return {
            "workflow": self.spec.name,
            "version": self.spec.version,
            "statuses": dict(self.statuses),
            "outputs": dict(self.outputs),
            "errors": dict(self.errors),
            "costs": dict(self.costs),
            "cost_usd": self.cost_usd,
            "events": list(self.events),
            "graph": self.graph(),
        }

    @staticmethod
    def _state_statuses(spec: WorkflowSpec, raw: object) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise WorkflowError("statuses 必须是对象")
        known = {step.id for step in spec.steps}
        statuses = {step_id: str(raw.get(step_id, "pending")) for step_id in known}
        allowed = {
            "pending", "running", "completed", "failed", "blocked",
            "cancelled", "paused", "budget-exceeded",
        }
        bad = [f"{name}={state}" for name, state in statuses.items() if state not in allowed]
        if bad:
            raise WorkflowError("未知状态: " + ", ".join(bad))
        return statuses

    @staticmethod
    def _state_maps(data: dict[str, Any], known: set[str]) -> tuple[dict, dict, dict]:
        outputs = data.get("outputs") or {}
        errors = data.get("errors") or {}
        if not isinstance(outputs, dict) or not isinstance(errors, dict):
            raise WorkflowError("outputs/errors 必须是对象")
        raw_costs = data.get("costs") or {}
        if not isinstance(raw_costs, dict):
            raise WorkflowError("costs 必须是对象")
        return (
            {str(k): str(v) for k, v in outputs.items() if k in known},
            {str(k): str(v) for k, v in errors.items() if k in known},
            {
                str(k): float(v) for k, v in raw_costs.items()
                if k in known and isinstance(v, (int, float))
            },
        )

    @classmethod
    def from_state(cls, spec: WorkflowSpec, data: dict[str, Any]) -> "WorkflowRun":
        if not isinstance(data, dict):
            raise WorkflowError("运行状态必须是 JSON 对象")
        if data.get("workflow") not in (None, spec.name):
            raise WorkflowError(
                f"状态属于 {data.get('workflow')!r}，不是 {spec.name!r}"
            )
        statuses = cls._state_statuses(spec, data.get("statuses") or {})
        known = {step.id for step in spec.steps}
        outputs, errors, costs = cls._state_maps(data, known)
        return cls(
            spec=spec,
            statuses=statuses,
            outputs=outputs,
            errors=errors,
            costs=costs,
            events=list(data.get("events") or []),
        )

def prepare_resume(run: WorkflowRun) -> WorkflowRun:
    """Reset unfinished/failed nodes to pending, keeping completed outputs."""
    completed = run.completed
    statuses = {
        step.id: ("completed" if step.id in completed else "pending")
        for step in run.spec.steps
    }
    return WorkflowRun(
        spec=run.spec,
        statuses=statuses,
        outputs={name: run.outputs[name] for name in completed if name in run.outputs},
        costs={name: run.costs[name] for name in completed if name in run.costs},
        events=list(run.events),
    )


def rerun_steps(run: WorkflowRun, step_ids: set[str]) -> WorkflowRun:
    """Reset nodes and their transitive dependents so a single node can re-run."""
    unknown = step_ids - {step.id for step in run.spec.steps}
    if unknown:
        raise WorkflowError("未知 step: " + ", ".join(sorted(unknown)))
    affected = set(step_ids)
    changed = True
    while changed:
        changed = False
        for step in run.spec.steps:
            if step.id not in affected and any(dep in affected for dep in step.needs):
                affected.add(step.id)
                changed = True
    statuses = dict(run.statuses)
    outputs = dict(run.outputs)
    errors = dict(run.errors)
    costs = dict(run.costs)
    for step_id in affected:
        statuses[step_id] = "pending"
        outputs.pop(step_id, None)
        errors.pop(step_id, None)
        costs.pop(step_id, None)
    return WorkflowRun(
        spec=run.spec,
        statuses=statuses,
        outputs=outputs,
        errors=errors,
        costs=costs,
        events=list(run.events),
    )


class _WorkflowEngine:
    """Own the scheduling loop and per-step retry state for one run."""

    def __init__(
        self,
        spec: WorkflowSpec,
        runner: Any,
        *,
        wf_span: Any,
        run: WorkflowRun,
        pending: set[str],
        stop_after: set[str],
        locks: WorkspaceLocks | None,
        semaphore: Any,
        cancel: Any,
        cost_limit_usd: float,
        cost_getter: Any,
        on_event: Any,
    ) -> None:
        self.spec = spec
        self.runner = runner
        self.wf_span = wf_span
        self.result = run
        self.pending = pending
        self.stop_after = stop_after
        self.locks = locks
        self.semaphore = semaphore
        self.cancel = cancel
        self.cost_limit_usd = cost_limit_usd
        self.cost_getter = cost_getter
        self.on_event = on_event

    def emit(self, kind: str, step: StepSpec, **extra: Any) -> None:
        payload = {"type": kind, "step": step.id, **extra}
        self.result.events.append(payload)
        if self.on_event is not None:
            self.on_event(payload)

    def cancelled(self) -> bool:
        if self.cancel is None:
            return False
        return bool(self.cancel() if callable(self.cancel) else self.cancel.is_set())

    def deps_outputs(self, step: StepSpec) -> dict[str, str]:
        return {
            name: self.result.outputs.get(name, "")
            for name in step.needs
            if name in self.result.outputs
        }

    async def _run_step_body(self, step: StepSpec) -> Any:
        if step.writes and self.locks is not None:
            with self.locks.acquire(step.writes):
                result = self.runner(step, self.deps_outputs(step))
                if inspect.isawaitable(result):
                    result = await result
                return result
        result = self.runner(step, self.deps_outputs(step))
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _execute(self, step: StepSpec) -> None:
        from spark.otel import end_step_span, start_step_span

        async with self.semaphore:
            self.result.statuses[step.id] = "running"
            self.emit("step_started", step)
            step_span = start_step_span(self.wf_span, workflow=self.spec.name, step=step.id)
            attempt = 0
            while attempt < step.max_attempts:
                attempt += 1
                try:
                    result = await self._run_step_body(step)
                    self.result.outputs[step.id] = str(result or "")
                    self.result.statuses[step.id] = "completed"
                    self.emit("step_completed", step, attempt=attempt)
                    end_step_span(
                        step_span,
                        status="completed",
                        cost_usd=self.result.costs.get(step.id, 0.0),
                    )
                    self.pending.discard(step.id)
                    return
                except Exception as exc:  # noqa: BLE001 - reported as step failure
                    self.result.errors[step.id] = str(exc)
                    self.emit("step_failed", step, attempt=attempt, error=str(exc))
            self.result.statuses[step.id] = "failed"
            end_step_span(
                step_span,
                status="failed",
                cost_usd=self.result.costs.get(step.id, 0.0),
            )
            self.pending.discard(step.id)

    def _ready_steps(self) -> list[StepSpec]:
        return [
            step
            for step in self.spec.steps
            if step.id in self.pending
            and all(self.result.statuses.get(dep) == "completed" for dep in step.needs)
        ]

    def _block_failed_dependents(self) -> None:
        for step in self.spec.steps:
            if step.id in self.pending and any(
                self.result.statuses.get(dep) == "failed" for dep in step.needs
            ):
                self.result.statuses[step.id] = "blocked"
                self.pending.discard(step.id)

    def _budget_break(self) -> bool:
        if self.cost_limit_usd <= 0:
            return False
        spent = float(self.cost_getter()) if callable(self.cost_getter) else self.result.cost_usd
        if spent < float(self.cost_limit_usd):
            return False
        for name in sorted(self.pending):
            self.result.statuses[name] = "budget-exceeded"
        self.emit(
            "run_budget_exceeded",
            self.spec.steps[0],
            spent=round(spent, 6),
            limit=round(float(self.cost_limit_usd), 6),
        )
        return True

    def _pause_break(self) -> bool:
        hit = [step for step in self.spec.steps if step.id in self.stop_after]
        if not hit or not self.pending or not any(
            self.result.statuses.get(step.id) == "completed" for step in hit
        ):
            return False
        for name in sorted(self.pending):
            self.result.statuses[name] = "paused"
        self.emit("run_paused", hit[0], pending=len(self.pending))
        return True

    def _cancel_break(self) -> bool:
        if not self.cancelled():
            return False
        for name in sorted(self.pending):
            self.result.statuses[name] = "cancelled"
        self.emit("run_cancelled", self.spec.steps[0], pending=len(self.pending))
        return True

    async def execute(self) -> WorkflowRun:
        import asyncio

        from spark.otel import end_workflow_span

        while self.pending:
            if self._cancel_break():
                break
            ready = self._ready_steps()
            if not ready:
                for name in sorted(self.pending):
                    self.result.statuses[name] = "blocked"
                break
            await asyncio.gather(*(self._execute(step) for step in ready))
            # A failed node must not leave its dependents runnable.
            self._block_failed_dependents()
            if self._budget_break() or self._pause_break():
                break
        end_workflow_span(self.wf_span, statuses=self.result.statuses, cost_usd=self.result.cost_usd)
        return self.result


async def run_workflow(
    spec: WorkflowSpec,
    runner: Any,
    *,
    workdir: Path | None = None,
    max_parallel: int = 2,
    cancel: Any = None,
    initial_outputs: dict[str, str] | None = None,
    initial_statuses: dict[str, str] | None = None,
    initial_costs: dict[str, float] | None = None,
    stop_after: set[str] | None = None,
    cost_limit_usd: float = 0.0,
    cost_getter: Any = None,
    on_event: Any = None,
) -> WorkflowRun:
    """Execute a validated DAG with bounded concurrency and per-step retries.

    ``runner(step, dependency_outputs)`` may be sync or async and must return
    text. The engine owns scheduling only: it never imports a provider or the
    AgentLoop, so it can be tested with fakes and reused by any surface.
    """
    import asyncio

    workers = max(1, min(int(max_parallel), 8))
    from spark.otel import start_workflow_span

    wf_span = start_workflow_span(
        name=spec.name, version=str(spec.version), steps=len(spec.steps), max_parallel=workers
    )
    initial = dict(initial_statuses or {})
    run = WorkflowRun(
        spec=spec,
        statuses={step.id: initial.get(step.id, "pending") for step in spec.steps},
        outputs=dict(initial_outputs or {}),
        costs=dict(initial_costs or {}),
    )
    pending = {
        step.id for step in spec.steps if run.statuses.get(step.id) != "completed"
    }
    engine = _WorkflowEngine(
        spec,
        runner,
        wf_span=wf_span,
        run=run,
        pending=pending,
        stop_after=set(stop_after or ()),
        locks=WorkspaceLocks(workdir) if workdir is not None else None,
        semaphore=asyncio.Semaphore(workers),
        cancel=cancel,
        cost_limit_usd=cost_limit_usd,
        cost_getter=cost_getter,
        on_event=on_event,
    )
    return await engine.execute()


# ---------------------------------------------------------------------------
# AgentLoop adapter
# ---------------------------------------------------------------------------


class AgentWorkflowRunner:
    """Adapter from workflow steps to ``AgentLoop.spawn_subtask``.

    Each step gets its own child context (so parallel experts do not share
    conversation state), the declared agent's prompt/tools/model when present,
    and dependency outputs in its task prompt.
    """

    def __init__(
        self,
        loop: Any,
        spec: WorkflowSpec,
        *,
        agents: dict[str, Any] | None = None,
        on_event: Any = None,
    ) -> None:
        self.loop = loop
        self.spec = spec
        self.agents = agents or {}
        self.on_event = on_event
        self.costs: dict[str, float] = {}

    @property
    def total_cost_usd(self) -> float:
        return round(sum(self.costs.values()), 6)

    def __call__(self, step: StepSpec, deps: dict[str, str]) -> Any:
        return self._run(step, deps)

    async def _run(self, step: StepSpec, deps: dict[str, str]) -> str:
        role = self._role(step)
        agent = self.agents.get(role.agent)
        prompt = self._prompt(step, deps)
        kwargs: dict[str, Any] = {}
        if agent is not None:
            kwargs["system_extra"] = getattr(agent, "body", "")
            tools = list(getattr(agent, "tools", []) or [])
            if tools:
                kwargs["tools_allowlist"] = tools
            model = getattr(agent, "model", "")
            if model:
                kwargs["model"] = model
        final = ""
        async for event in self.loop.spawn_subtask(prompt, **kwargs):
            if event.type == "turn_end":
                final = event.text or final
                usage = getattr(event, "data", None)
                if isinstance(usage, dict):
                    cost = usage.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        self.costs[step.id] = round(float(cost), 6)
            elif self.on_event is not None and event.type in {
                "tool_start", "tool_end", "plan", "approval_needed"
            }:
                self.on_event(step.id, event)
        return final

    def _role(self, step: StepSpec) -> RoleSpec:
        return self.spec.roles[step.role]

    def _prompt(self, step: StepSpec, deps: dict[str, str]) -> str:
        parts = [
            f"你是工作流步骤 {step.id}，角色是 {step.role}。",
            step.prompt,
        ]
        if deps:
            parts.append("上游步骤的输出：")
            for name, output in sorted(deps.items()):
                parts.append(f"<{name}>\n{output[:4000]}\n</{name}>")
        if step.writes:
            parts.append("你只能写入这些路径： " + ", ".join(step.writes))
        parts.append("完成后用简短文字总结改动、验证结果和剩余风险。")
        return "\n\n".join(parts)

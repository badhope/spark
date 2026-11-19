# Multi-agent workflows

W6 starts with a declaration layer that can be checked without calling a model.

```bash
spark workflow validate examples/workflows/review.yaml
spark workflow graph examples/workflows/review.yaml
spark workflow run examples/workflows/review.yaml --max-parallel 2
```

## YAML shape

```yaml
name: review-change
version: 1
roles:
  lead: {agent: lead}
  backend: {agent: backend, tools: [read_file, apply_patch, run_tests]}
steps:
  - id: plan
    role: lead
    prompt: Break the change into acceptance criteria.
    writes: [.spark/plan.md]
  - id: implement
    role: backend
    needs: [plan]
    prompt: Implement the plan.
    writes: [src/]
```

Validation rejects duplicate/unknown step ids, unknown roles, missing dependencies,
cycles, unsafe write globs and invalid retry counts. `workflow graph` emits a
stable `{nodes, edges}` shape for the execution graph.

## Path locks

`spark.workflow.WorkspaceLocks` takes locks in sorted path order and rejects
paths outside the workdir, so two workers cannot deadlock on overlapping sets
or silently overwrite the same file. Locks live under `.spark/locks/` and are
cross-process (`filelock`).

## Status

- done: YAML parsing, DAG validation, topological order, ready-step calculation,
  graph output, cross-process path locks, CLI `validate/graph`.
- done: `run_workflow` engine with bounded concurrency, per-step retries,
  failure -> dependent blocked, cancellation and per-step write locks.
- done: `AgentWorkflowRunner` wires each step to `AgentLoop.spawn_subtask`;
  `spark workflow run` executes the DAG with child contexts and role tools/model.
- next: a real provider-backed end-to-end run, pause/resume/replay of individual
  nodes and cost budgets.

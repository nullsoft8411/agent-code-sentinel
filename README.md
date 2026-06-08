# Agent Code Sentinel Runtime

Purpose: provide the portable command layer that lets the Code Sentinel
Workspace Agent follow the local Code Sentinel workweise without pretending it
owns the full production SaaS runtime. This repository is intended to be cloned
by the Workspace Agent through PIKA MCP and executed from the cloned checkout.

This runtime is a local adapter. Agent Studio and Slack remain the human-facing
agent surfaces; the adapter provides deterministic state, command output, and
write-approval checks when the Workspace Agent runtime uses it.

## State

- Default local database: `state/code_sentinel_agent.db`
- Test databases: pass `--db <path>` to commands that read SQLite state.
- Artifacts: use `runs/` for per-run evidence.
- Secrets: never write secret values into SQLite, artifacts, or uploaded Agent
  Studio files. Store only source labels and blocker states.

## Migration Analysis

- [Local Code Sentinel Migration Analysis](docs/local-code-sentinel-migration-analysis.md)
  maps the local `/home/pika/projekte/code-sentinel` SaaS runtime to the
  agent-native `agent-code-sentinel` runtime, including schema gaps, module
  gaps, MCP-state boundaries, and phased acceptance gates.
- [Local Code Sentinel Source Inventory](docs/local-code-sentinel-source-inventory.json)
  is generated from the local source tree and captures SQLAlchemy table coverage
  plus runtime module coverage for migration planning.
- [Agent-Native Code Sentinel Migration Plan](docs/agent-native-code-sentinel-migration-plan.md)
  defines the required replacement for the old external Claude execution path:
  the Workspace Agent performs analysis, finding creation, task/subtask planning,
  validation decisions, and autonomous next-step selection itself.

## Command Layer

Run commands with the package source on `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli detect-checks --path /path/to/repo
```

Preflight a target repository:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli preflight --project /path/to/repo
```

Analyze Agent-provided context into normalized findings and a fix plan:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli analyze-context --input-json '{"project_id":"proj-1","run_id":"run-1","context":{"files":[{"path":"src/app.py","content":"API_KEY=redacted-example"}]}}'
```

Build the exact analysis contract that the Workspace Agent must fill itself:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli analysis-contract \
  --project-id proj-1 \
  --run-id run-1 \
  --target-project owner/repo \
  --file src/app.py \
  --validation-command "python3 -m pytest tests -q"
```

The contract output is not an executor prompt for any external AI process. It
tells the Workspace Agent what evidence to read and how to format its own
`finding_candidates`.

Persist the Agent's completed findings into shared state, create tasks, and
select the next takeover item:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli analyze-to-state \
  --db /tmp/runtime.db \
  --input-json '{"project_id":"proj-1","run_id":"run-1","finding_candidates":[{"category":"security","severity":"high","file_path":"src/app.py","line_number":1,"title":"Hardcoded token","description":"A token-like value is stored in source.","evidence":"TOKEN=[REDACTED]","source":"agent_reasoning","rule_id":"secret_assignment"}]}'
```

Evaluate persisted QA gate state:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli qa-gates --db /tmp/runtime.db --run-id run-1
```

Process a validation result into QA gate state, findings, tasks and the next
Agent takeover task:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli qg-workflow --db /tmp/runtime.db --payload-json '{"project_id":"proj-1","run_id":"run-1","gate":"validation","command":"pytest tests -q","exit_code":1,"stderr":"src/app.py:1: AssertionError"}'
```

Create tasks and subtasks from open findings:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli create-tasks --db /tmp/runtime.db --project-id proj-1 --run-id run-1
```

Record an agent-native execution session:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli execution-session --db /tmp/runtime.db --payload-json '{"id":"session-1","project_id":"proj-1","run_id":"run-1","session_type":"analysis","script_name":"analyze-context","execution_method":"python_executed_from_cloned_repo","command":"PYTHONPATH=src python3 -m code_sentinel_agent.cli analyze-context","status":"passed","output":{"status":"passed"}}'
```

Check whether a write is explicitly approved:

```bash
PYTHONPATH=src \
python3 -m code_sentinel_agent.cli approval-check \
  --db /tmp/runtime.db \
  --run-id run-1 \
  --target-project owner/repo \
  --branch code-sentinel/run-1 \
  --path docs/proof.md \
  --action file_write
```

Read memory delta and run report:

```bash
PYTHONPATH=src \
python3 -m code_sentinel_agent.cli memory-delta --db /tmp/runtime.db --project-id proj-1

PYTHONPATH=src \
python3 -m code_sentinel_agent.cli report --db /tmp/runtime.db --run-id run-1
```

Resume an autonomous cycle from SQLite memory:

```bash
PYTHONPATH=src \
python3 -m code_sentinel_agent.cli resume-cycle \
  --db /tmp/runtime.db \
  --project-id proj-1 \
  --run-id run-next \
  --latest-ref main@new
```

Run one autonomous cycle step with lock, state, task selection and optional
validation/write evidence:

```bash
PYTHONPATH=src python3 -m code_sentinel_agent.cli run-cycle --db /tmp/runtime.db --payload-json '{"project_id":"proj-1","run_id":"run-1","latest_ref":"main@new"}'
```

Expose the same central SQLite state through MCP-style tool calls:

```bash
PYTHONPATH=src \
python3 -m code_sentinel_agent.cli mcp-state \
  --db state/code_sentinel_agent.db \
  --tool state_lock_acquire \
  --payload-json '{"project_id":"proj-devopshub","run_id":"run-1","owner":"code-sentinel-agent"}'

PYTHONPATH=src \
python3 -m code_sentinel_agent.cli mcp-state \
  --db state/code_sentinel_agent.db \
  --tool state_run_start \
  --payload-json '{"project_id":"proj-devopshub","run_id":"run-1","latest_ref":"main@new"}'
```

Available MCP-state tools:

- `state_project_get`
- `state_memory_get`
- `state_lock_acquire`
- `state_lock_release`
- `state_run_start`
- `state_analyze_to_state`
- `state_append_event`
- `state_scan_job_create`
- `state_scan_finding_upsert`
- `state_scan_findings_list`
- `state_scan_finding_status_update`
- `state_tasks_create_from_findings`
- `state_tasks_list`
- `state_task_status_update`
- `state_approval_record`
- `state_task_execution_result`
- `state_qa_gate_process`
- `state_execution_session_record`
- `state_execution_sessions_list`
- `state_report_get`
- `state_audit_event_append`
- `state_audit_events_list`
- `state_pr_state_set`
- `state_pr_state_get`

Parallel run rule: every write-capable autonomous run must acquire
`state_lock_acquire` for its project first. If another run holds the lock, the
valid result is `blocked` with `STATE_LOCK_HELD`; the agent must not write a
separate copied SQLite state and must not claim shared persistence.

Backend rule: plain file paths use the SQLite proof backend. `postgresql://` or
`postgres://` DSNs use the local `psql` client as a dependency-light Postgres
backend. The DSN is parsed into `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and
`PGPASSWORD` environment variables instead of being passed as a process
argument, so credentials are not exposed through the command argv.

Postgres schema preparation lives in
`migrations/postgres/001_mcp_state.sql`. It mirrors
the MCP-state tables and documents the intended project-level transaction lock:
`pg_try_advisory_xact_lock(hashtext(:project_id))`. A real Postgres adapter must
take that lock in the same transaction before creating runs, appending events,
or updating shared project state.

The current `psql` backend implements `state_project_get`,
`state_lock_acquire`, `state_lock_release`, and `state_run_start`; tools
outside that set still return a controlled `blocked` response until
implemented.

## Output Rules

- JSON stdout is the machine contract.
- Exit `0` means the requested adapter check passed.
- Exit `2` means the adapter found a controlled blocker, such as missing
  project path, blocking QA gate, or missing write approval.
- A Workspace Agent must report `static_only` when it can read these files but
  has no real execution tool evidence.
- A Workspace Agent must report `blocked` when the requested action depends on
  unavailable local runtime access, missing approval, or missing repository
  truth.
- MCP-state DB access is the preferred shared-state path for parallel agent
  runs. The uploaded SQLite seed file remains a bootstrap/fallback artifact, not
  the primary multi-run write target.

## Validation

Focused runtime validation:

```bash
PYTHONPATH=src python3 -m pytest tests -q
```

Repo validation:

```bash
PYTHONPATH=src python3 -m pytest tests -q
```

Agent/MCP DB proof script:

```bash
python3 scripts/agent_mcp_result_probe.py --mcp-json '{"project_result":{"project":{"id":"proj-agent-e2e","target":"workspace-agent-script-e2e","default_branch":"main"}},"memory_result":{"memory":{"latest_ref":"main@agent-e2e","stale_memory_decision":"fresh"}}}'
```

Agent-native runtime cycle proof script:

```bash
PYTHONPATH=src python3 scripts/agent_runtime_cycle_smoke.py
```

This script seeds a temporary local state database and fixture repository, reads
project context, persists Agent-supplied finding candidates through
`analyze_to_state`, runs `state_run_cycle` equivalent runtime logic, and returns
JSON evidence for selected task takeover, QA review outcomes, report readback
and lock release. It is designed to run from a cloned checkout and does not use
Claude, tmux, MCP `run_command`, or another external AI executor.

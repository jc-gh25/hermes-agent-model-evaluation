# Hermes Agent Model Evaluation

Experimental local evaluation of tool-using models through the Hermes Agent. Each evaluation runs Hermes inside a pinned, rootless Podman container, retains all evidence on the host, grades results independently on the host, and can export CSV summaries and an offline HTML dashboard.

This project is experimental. It is not a general model leaderboard and not a hardened security sandbox.

## What does it evaluate?

The current task, `exact-file-v2` (`tasks/exact-file-v2.json`), asks the agent to:

- use `write_file` to create `/opt/data/workspace/agent-proof.txt` containing exactly `container-agent-ok` followed by exactly one newline (LF),
- use `read_file` to read that same file back,
- use no shell, code execution, or another method to create the file.

Only `write_file` and `read_file` are allowed. The host grader independently checks the final file bytes against the task's expected bytes, the completed write/read tool sequence, tool-policy compliance, and the requested model and endpoint. The full task fixture and grading reference remain host-only. The container receives the task prompt, which necessarily specifies the requested file contents, and the derived execution limits.

## What are its current capabilities and limitations?

Capabilities:

- Authoritative, versioned task definitions loaded by `tasklib.py` (standard library only, safe to import).
- Host-only task snapshot: the runner validates the task, copies the exact task bytes into the run directory, and derives the prompt and per-step limits from those same bytes; it also validates that the step budgets plus overhead fit the container budget.
- Hardened host-side grading (`grade_v2.py`, grader version 2.0.0): final bytes, tool sequence, tool policy, termination classification, and requested model/endpoint — seven checks in total.
- Three separate timing measures (see below), with agent-process time measured by a monotonic clock.
- Per-run evaluator provenance and privacy-safe reporting (see below).

Limitations:

- One task, one run at a time; no concurrency or resume support.
- Configuration values (quantization, sampling, MTP, GPU placement, context size, KV precision) are recorded as `unknown` unless recorded manually; they are never inferred from model names.
- Real-run evidence exists only for the normal path. Timeout (exit 124) and SIGKILL (exit 137) handling, and the provenance capture in the current code, have not been exercised by a real run; they are covered by stubbed integration tests and synthetic no-model tests only.
- The worker can edit its own session/usage evidence inside its mount; host-side grading is independent, but the setup is not tamper-proof.
- The proof-file path handling in `grade_v2.py` is a best-effort containment check, not a complete malicious-worker security boundary.
- Egress from the container is unrestricted; the worker receives its API key via the environment; container resource limits do not constrain the separate LM Studio server; the existing Hermes home, source checkout, and runtime sockets are not mounted; there are no automatic host application updates.

## What prerequisites are needed?

- A Linux host with rootless Podman, and `bash`, `python3`, `timeout`, `tee`, and `mktemp` on PATH.
- The pinned agent image already present locally (no pull is attempted):
  `docker.io/nousresearch/hermes-agent@sha256:7c6c7debc9e8fe623ffd5afce1c80116e8cff4e818ef42eb649d43562139d140`
- An LM Studio server with the model to evaluate, reachable from the host. Two per-run inputs are required and are never committed: the API token (`LM_API_KEY`) and the host-reachable endpoint (`LM_BASE_URL`).
- Hermes Agent requires a model/service with at least 64K-token context capability (operator-recorded requirement; see [HANDOFF-v2.md](HANDOFF-v2.md)).
- A checkout of this project (convention: `~/hermes-eval`).

## How do I run one v2 evaluation?

```bash
read -rsp 'LM Studio token: ' LM_API_KEY
printf '\n'
export LM_API_KEY
export LM_BASE_URL='http://<lm-studio-host>:1234/v1'   # documentation placeholder: use your host-reachable endpoint
bash "$HOME/hermes-eval/run_v2.sh" <model-id>           # the exact model ID is a required argument
unset LM_API_KEY LM_BASE_URL
```

The token is entered privately and never appears in a command, a file, or a commit. Setting `LM_BASE_URL` applies only to this run; it does not change your normal Hermes configuration.

Before creating a run directory or starting a container, the runner validates the token, the model ID, the endpoint (non-empty, `http(s)://`), the pinned image, and the task snapshot. It then:

1. validates the task with `tasklib` and snapshots the exact task bytes to `<run>/task.json` (host-only, never mounted),
2. derives the prompt and per-step limits from the same bytes and validates limit consistency,
3. records run metadata (including provenance, the endpoint, and the derived limits) in `<run>/metadata.json`,
4. runs the container with only the worker home mounted at `/opt/data` (prompt and derived limits delivered into it), under `--cpus=2 --memory=2g --memory-swap=2g --pids-limit=100` as user 10000:10000,
5. exports the session trace, removes the container, and grades the run on the host.

Evidence is retained under `runs/run.XXXXXXXX/` (`metadata.json`, `task.json`, `derived.json`, `worker-home/`, `combined_output.log`, `result.json`). Runner output is wrapped in `BEGIN OUTPUT TO COPY` / `END OUTPUT TO COPY` markers. Neither the runner nor the worker prints tokens.

## How do I interpret outcomes and the three timing measures?

- Runner exit 0 means the evaluation completed — inspect `result.json` for the task outcome. Runner exit 3 means a prerequisite, evidence, or infrastructure failure. Runner completion is distinct from task pass/fail: a candidate failure is a valid evaluation result, and infrastructure errors are separate.
- Task outcomes: `pass` or `fail`. `infrastructure_error` marks unusable evidence (for example a failed trace export), not a model failure.
- `agent_termination`: `normal`, `timeout` (exit 124 from the wrapper), `sigkill` (exit 137 — recorded separately and NOT a confirmed timeout), `error`, or `unknown`.
- A missing final LF produces outcome `fail` (the bytes differ), not `infrastructure_error`.
- The seven grading checks: `agent_exit_zero`, `exact_bytes`, `write_tool_completed`, `read_after_write_completed`, `requested_model`, `expected_endpoint`, `allowed_tools_respected`.
- Timing is three separate measures and they are not interchangeable:
  - total workflow (host-side): container startup + agent + trace export;
  - agent-process (monotonic clock): the agent-process duration inside the worker;
  - session-reported: the duration the session reports inside the exported trace.
- Configuration values can remain `unknown`; groups with unknown settings may mix different real configurations.

## How do I generate and open reports/dashboard?

```bash
python3 "$HOME/hermes-eval/model_eval_report.py"    # optional argument: alternate project directory
python3 "$HOME/hermes-eval/model_eval_dashboard.py" # optional argument: alternate project directory
```

The reporter reads every `runs/*/result.json` and writes a new timestamped directory `reports/report-<UTC timestamp>/` containing `runs.csv` (one row per attempt), `summary.csv` (one row per task / task version / grader version / recorded configuration / code fingerprint group), and `REPORT_NOTES.md`. It runs no inference and modifies no evidence.

The dashboard reads the latest report (or a `summary.csv` given beside it) and writes a single self-contained, offline `dashboard-<UTC timestamp>.html` beside those CSVs; double-click the file to open it in a browser. It sorts and filters in the page and makes no network requests. The dashboard exits with an error if no report exists yet — run the reporter first.

Both tools default to `~/hermes-eval` when given no argument; pass the project directory as the optional first argument to use a checkout kept elsewhere. The runner itself always writes evidence beneath its own directory.

## How do I run the no-model tests?

```bash
python3 -m unittest discover -s tests -v   # recorded validation: 68 passing tests
bash -n run_v2.sh worker_v2.sh             # shell syntax check
python3 tasklib.py validate tasks/exact-file-v2.json
```

The 68-test count is a recorded validation from a previous session (loader 8, grader 34, integration 9, report-privacy 17); it was not re-run here. The integration suite is stubbed — it replaces `podman` and `hermes` with fakes and validates pipeline wiring only, not real Podman or Hermes execution or real inference.

## How do provenance and reporting privacy work?

Each run records code provenance in `metadata.json` (which `grade_v2.py` copies through to `result.json`):

- `code_fingerprint_scheme` (`v1`) and `code_fingerprint`: scheme `v1` is `sha256` over the sorted `(filename, sha256(content))` pairs of `run_v2.sh`, `worker_v2.sh`, `grade_v2.py`, `tasklib.py`, and the exact per-run task-snapshot bytes under the stable name `task_snapshot.json`. It is computed from actual file bytes, so uncommitted edits are captured. Report/dashboard code, credentials, endpoints, environment dumps, and unrelated files are excluded. A computation failure is recorded as `unknown` — never a crash and never a false fingerprint.
- `code_commit` and `code_tree_dirty`: the Git revision and dirty state at run time, or `unknown` when Git information is unavailable (never a false `clean`).
- Content fingerprints and Git commits serve different purposes: the fingerprint identifies the evaluation code and task bytes actually used; the commit identifies the source revision. Runs group by fingerprint scheme + fingerprint, so a documentation-only commit (same fingerprint) does not split an equivalent implementation, while a real code change does.
- Legacy runs are not retrofitted with provenance. All six retained real runs predate provenance capture and therefore carry `unknown` recorded provenance.

Reporting privacy:

- `infrastructure_errors` is exported as a safe count only (in `runs.csv` and `summary.csv`); raw error messages remain only in the local `result.json`. A legacy result with no `infrastructure_errors` field counts as zero. A present non-list value (including null) is invalid report input: the attempt is exported with outcome `invalid_data` — unscored (not a pass, valid attempt, successful timed run, or in any success percentage or timing median) but visible in total attempts — and is neither a model failure nor an infrastructure error.
- Evidence identifiers (`runs/<run-id>`) are derived from where each result actually lives, never from metadata strings, so they stay valid when the project is moved. Generated reports contain no absolute home paths.
- CSV values beginning with `=`, `+`, `-`, or `@` are written with a leading apostrophe to prevent spreadsheet formula injection.
- These controls are not universal free-text sanitization. Detailed local evidence (`result.json`, traces, combined logs) may still contain private information, and previously generated reports are not sanitized retroactively.

## Where are development notes and licensing information?

- [HANDOFF-v2.md](HANDOFF-v2.md) holds historical run IDs, operator/session notes, environment details, and the responsive local configuration. Retained local runs (`runs/`) and generated reports (`reports/`) are git-ignored and are not distributed in the repository.
- Post-cleanup pilot (2026-10-11): three runs (`runs/run.5W8oobTG`, `runs/run.TQQj4VI3`, `runs/run.JHZ5yJ2U`) all passed this narrow task; agent-process durations were 22.462 s, 14.08 s, and 36.204 s respectively. Three passes on one narrow tool-use task are not general model reliability.
- License: MIT — see [LICENSE](LICENSE) (Copyright (c) 2026 jc-gh25).

## Legacy v1 entry point (preserved)

v1 (`run.sh`, `worker.sh`, `grade.py`, `tasks/exact-file-v1.json`) hardcodes its task values. Its retained runs (`runs/run.YFQhuYf4`, `runs/run.xGecy3nC`) were produced by that path and are kept as evidence; v1 is not under active development. Do not mix generations: the v2 runner always grades with `grade_v2.py`, never `grade.py`.

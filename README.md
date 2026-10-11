# Hermes local evaluation prototype

Experimental. Not a security boundary or a model leaderboard.

## Entry points

Two generations coexist. Do not mix them.

**v1 (legacy, preserved):** `run.sh`, `worker.sh`, `grade.py`,
`tasks/exact-file-v1.json`. Task values are hardcoded. The saved runs
`runs/run.YFQhuYf4` and `runs/run.xGecy3nC` were produced by this path.
Kept as evidence; not under active development.

**v2 (current):** `run_v2.sh`, `worker_v2.sh`, `grade_v2.py`, `tasklib.py`,
`tasks/exact-file-v2.json`. The task definition is authoritative and
versioned. The runner validates and snapshots it host-only, derives the
metadata, prompt, and limits from the exact same snapshot bytes, validates
limit consistency, measures agent-process time with a monotonic clock, and
grades with `grade_v2.py` (never `grade.py`).

Both preserve the exit contract (0 = evaluation completed, 3 =
infrastructure error) and the BEGIN/END OUTPUT TO COPY markers. Neither
prints tokens.

## Run (v2)

Keep this project in `~/hermes-eval`. The runner needs two local
inputs, supplied per run and never committed: the LM Studio API token
(`LM_API_KEY`) and your host-reachable LM Studio endpoint
(`LM_BASE_URL`). Setting `LM_BASE_URL` here does not change your
normal Hermes configuration — the runner reads it only for the run.

```bash
read -rsp 'LM Studio token: ' LM_API_KEY
printf '\n'
export LM_API_KEY
export LM_BASE_URL='http://<lm-studio-host>:1234/v1'   # your endpoint
bash "$HOME/hermes-eval/run_v2.sh" <model-id>            # model ID is required
unset LM_API_KEY LM_BASE_URL
```

The runner validates `LM_BASE_URL` (non-empty, `http(s)://`) and the
model ID before creating a run directory or starting a container, and
records the actual selected endpoint in `metadata.json`. The pinned
image must already be local; no packages or images are installed. The
task snapshot (`tasks/exact-file-v2.json`) and the grader stay on the
host, outside the worker mount; only the prompt and the derived per-step
limits enter the container. A run directory is created under `runs/`;
grading is host-only.

## Outcomes

- Runner exit 0 means evaluation completed; inspect `result.json` for
  pass/fail. Runner exit 3 means a prerequisite/evidence or infrastructure
  failure.
- `grade_v2.py` records `agent_termination`: `normal`, `timeout` (exit 124
  from the wrapper), `sigkill` (exit 137 — recorded separately and NOT a
  confirmed timeout), `error`, or `unknown`.
- Timing is three separate measures: total workflow (host-side), agent-process
  (monotonic clock), and session-reported (inside the trace). They are not
  interchangeable.

## Run provenance and reporting privacy

Each run records code provenance in `metadata.json` (which
`grade_v2.py` copies through to `result.json`):

- `code_fingerprint_scheme`: the fingerprint scheme version
  (`v1`).
- `code_fingerprint`: scheme `v1` is `sha256` over the sorted
  `(filename, sha256(content))` of `run_v2.sh`, `worker_v2.sh`,
  `grade_v2.py`, `tasklib.py`, and the exact per-run task
  snapshot bytes (the snapshot copied into the run directory
  and used for the run), under the stable input name
  `task_snapshot.json`. It is computed from actual file bytes,
  so uncommitted edits are captured. Report and dashboard code,
  credentials, endpoints, environment dumps, and unrelated
  files are excluded. A fingerprint computation failure is
  recorded as `unknown` -- never a crash and never a false
  fingerprint.
- `code_commit`: the Git commit at run time, or `unknown` when
  Git information is unavailable (never a false `clean`).
- `code_tree_dirty`: `true` / `false` / `unknown` (unknown when
  Git information is unavailable).
- Scheme versioning: `code_fingerprint_scheme` is `v1`. The v1
  definition was refined (to hash the exact per-run snapshot
  bytes under the stable name `task_snapshot.json`, rather than
  re-reading the source fixture path) before any commit or saved
  run used it, so no previously committed or saved `v1`
  fingerprint exists to be invalidated.
- `infrastructure_errors` is exported as a safe count only (in
  both `runs.csv` and `summary.csv`). A legacy result with no
  `infrastructure_errors` field counts as zero. A present
  non-list value (including null) is invalid report input: the
  attempt is exported with outcome `invalid_data` and counted in
  `summary.csv`'s `invalid_data` column — unscored (not a pass,
  valid attempt, successful timed run, or in any success
  percentage or timing median), but visible in total attempts and
  the invalid-data count. It is not a model failure and not an
  infrastructure error. The raw malformed value is never exported
  (the per-run count shows `unknown`). Raw infrastructure-error
  messages are never exported to any report, CSV, notes, or
  dashboard HTML; they remain only in the local `result.json`.

The reporter (`model_eval_report.py`) derives each evidence
identifier from where the result actually lives
(`runs/<run-id>`), never from a metadata string, so it stays
valid when the project is moved. Generated reports contain no
absolute home paths.

Runs group by fingerprint scheme + fingerprint alongside the
task/grader/model/configuration keys, so a documentation-only
commit (same fingerprint) does not split an equivalent
implementation, while a real code change (different fingerprint)
does. Runs without a recorded fingerprint show `unknown
provenance` and stay distinct from runs with a recorded
fingerprint. Provenance is never invented or inferred from
timestamps; legacy runs keep unknown provenance. In the summary
table, all distinct recorded commits and dirty states are
listed (not one attempt's values); individual evidence
identifiers live in the per-run table.

## Testing (no model)

```bash
python3 -m unittest discover -s tests -v   # loader + grader + integration suites
bash -n run_v2.sh worker_v2.sh             # shell syntax
python3 tasklib.py validate tasks/exact-file-v2.json
```

The integration test (`tests/test_integration_v2.py`) is STUBBED: it
replaces `podman` and `hermes` with fakes and does not validate real
Podman or Hermes execution or real inference.

## Limits

One task, one run, no concurrency or resume support. GPU/sampling/MTP/context
settings are unknown until manually recorded. The worker can edit its own
session/usage evidence; this is not tamper-proof. The proof-file symlink
check in `grade_v2.py` is a best-effort containment check, NOT a complete
malicious-worker security boundary. Egress is unrestricted; the worker has
its dedicated API key. Container limits do not constrain the separate LM
Studio server. Existing Hermes home, source checkout, and runtime sockets
are not mounted. No automatic host application updates.

## First validation

Run once and inspect output, `result.json`, the trace, and cleanup. A missing
LF should produce outcome `fail`, not `infrastructure_error`. Exit 137 is
recorded as `sigkill`, not a confirmed timeout.

## Real v2 validation (one smoke-test pass)

The first real v2 inference run completed on 2026-10-11:

- Task: exact-file-v2, task version 1
- Model: mellum2.1-12b-a2.5b-thinking
- Agent-process duration: 23.248 s (monotonic clock); total workflow 27.18 s
- Agent exit: 0; trace export exit: 0
- All seven grading checks passed (including allowed_tools_respected)
- Expected and actual bytes matched
- Container removed; no infrastructure errors
- Overall outcome: pass
- Evidence: runs/run.wj9KLui7

This is one real smoke-test pass on one narrow tool-use task. It is NOT
general model reliability. To reproduce:

```bash
read -rsp 'LM Studio token: ' LM_API_KEY
printf '\n'
export LM_API_KEY
export LM_BASE_URL='http://<lm-studio-host>:1234/v1'   # your endpoint
bash "$HOME/hermes-eval/run_v2.sh" mellum2.1-12b-a2.5b-thinking
unset LM_API_KEY LM_BASE_URL
```

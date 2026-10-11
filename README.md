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

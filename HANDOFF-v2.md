# Hermes Agent Model Evaluation — Handoff

> **Read this first.** This document is the current local-project orientation for a new human or agent. Read it before changing scripts, rerunning inference, or deleting artifacts.

## Current status

**Project state:** Experimental local evaluation harness. Stage 1A (task loader), Stage 1B (standalone grader), and Stage 2 (v2 runner/worker integration) are complete. The first real v2 inference run (Mellum, `runs/run.wj9KLui7`) passed on 2026-10-11 — one smoke-test pass, not general model reliability.

**Primary next goal:** Prepare the project for a private GitHub repository (readiness review in progress). Further real runs across models and configurations are needed before any reliability claims.

**Do not do first:** Do not rerun the canonical smoke test merely to see whether it still works. Do not overwrite any retained run directory. Do not use or disclose an API token in chat, source, shared logs, or a Git commit.

## Environment and locations

Work was done in the **Ubuntu VM**.

```text
Project:          ~/hermes-eval
Prototype archive: local archive (not committed)
Canonical run:    ~/hermes-eval/runs/run.YFQhuYf4
```

The evaluation invokes a rootless Podman container; inference is provided by a local LM Studio server. The endpoint is a required per-run input supplied via `LM_BASE_URL` (not committed):

```text
<your host-reachable LM Studio endpoint, e.g. http://<host>:1234/v1>
```

Pinned agent image:

```text
docker.io/nousresearch/hermes-agent@sha256:7c6c7debc9e8fe623ffd5afce1c80116e8cff4e818ef42eb649d43562139d140
```

Image-reported Hermes version:

```text
vgit.90194c6 — upstream 90194c64 — 2026.9.24
```

The image revision and any local Hermes checkout revision must be recorded separately; do not assume they match.

## Context requirement

Hermes Agent requires a model/service with at least a **64K-token context capability** for its intended tool-using workflow. Do not solve performance by configuring a sub-64K context for Hermes.

Distinguish these two things:

- **Model/context capability:** Hermes needs the provider/model setup to support at least 64K.
- **Practical performance:** A 64K-plus context may be very slow if model weights or the KV cache spill from VRAM into system RAM.

The display was moved to the separate 16 GB GPU; the RTX 3070, but still, a long-context thinking model with KV cache outside VRAM can make the desktop unresponsive. For routine agent work, prefer a model/configuration that remains responsive while honestly retaining Hermes-compatible context support; reserve slow long-context thinking tests for deliberately bounded work.

## Existing project files

```text
~/hermes-eval/
├── README.md                  # Prototype operation and limitations (v1 + v2 entry points)
├── HANDOFF-v2.md              # This document
├── run.sh                     # v1 host runner (legacy, preserved)
├── worker.sh                  # v1 container worker (legacy, preserved)
├── grade.py                   # v1 host grader (legacy, preserved)
├── run_v2.sh                  # v2 host runner: validates+snapshots task, derives metadata/prompt/limits, invokes grade_v2
├── worker_v2.sh               # v2 container worker: snapshot-derived prompt/limits, monotonic agent timing
├── grade_v2.py                # v2 host grader: byte + trace + tool-policy + termination classification
├── tasklib.py                 # Authoritative task-definition loader (stdlib-only, safe to import)
├── model_eval_report.py       # CSV summary reporter (groups by task/version/grader/configuration)
├── model_eval_dashboard.py    # Offline HTML dashboard generator
├── tasks/
│   ├── exact-file-v1.json     # v1 task fixture (legacy)
│   └── exact-file-v2.json     # v2 authoritative task fixture
├── tests/                     # No-model suites: loader, grader, stubbed integration
└── runs/
    ├── run.YFQhuYf4/          # v1 canonical successful validation
    └── run.xGecy3nC/          # v1 Mellum successful validation
```

The v1 fixture is still described by `tasks/exact-file-v1.json`, but the v1 worker and grader hardcode important task values. The v2 path (`run_v2.sh`/`worker_v2.sh`/`grade_v2.py`) uses the authoritative `tasks/exact-file-v2.json` instead.

## Canonical successful run

Preserve:

```text
~/hermes-eval/runs/run.YFQhuYf4
```

Evaluated model (exact ID, as recorded):

```text
qwen3.8-35b-a3b-distill-mtp-apex-i-miniplus-v2.1@?
```

Its recorded result was:

```text
outcome: pass
agent_exit_zero: true
exact_bytes: true
write_tool_completed: true
read_after_write_completed: true
requested_model: true
expected_endpoint: true
infrastructure_errors: []
```

The smoke task required the container-side path:

```text
/opt/data/workspace/agent-proof.txt
```

to contain exactly:

```python
b"container-agent-ok\n"
```

The host grader independently confirmed the exact final bytes and checked a completed `write_file` result followed by a completed `read_file` result for that path. The model's prose wrongly described a byte-count discrepancy, but the actual deliverable and checked tool sequence passed. Keep those facts separate.

## v2 integration (Stage 2 — complete, no inference)

Stage 2 added a separate v2 entry point without touching the v1 runner,
worker, grader, task fixtures, or saved results. New files: `run_v2.sh`,
`worker_v2.sh`, `grade_v2.py` (Stage 1B), `tasklib.py` (Stage 1A),
`tasks/exact-file-v2.json`, `tests/test_grader_v2.py`,
`tests/test_integration_v2.py`, plus reporting/dashboard updates.

**v2 runner (`run_v2.sh`):** validates `tasks/exact-file-v2.json` with
`tasklib.load_task()` before execution; snapshots the exact task bytes to
`<run>/task.json` (host-only, never mounted); derives metadata, prompt, and
per-step limits from the same snapshot bytes; records `task_fixture_sha256`
and task id/version in `metadata.json` (which `grade_v2` re-validates);
validates limit consistency (`agent_seconds + export_seconds + overhead <=
container_seconds`); keeps the snapshot and grader outside the worker mount;
invokes `grade_v2.py` (never `grade.py`); preserves the 0/3 exit contract
and output-copy markers.

**v2 worker (`worker_v2.sh`):** reads the prompt from `/opt/data/prompt.txt`
and per-step limits from `AGENT_SECONDS`/`EXPORT_SECONDS` (both derived
from the snapshot); measures agent-process duration with a monotonic clock
(`time.monotonic`) and preserves the agent exit status; keeps session-reported
duration (in the trace) and total workflow time (host-side) separate from
agent-process time.

**Stage 2 grader corrections:** exit 124 is a confirmed timeout; exit 137
(SIGKILL) is recorded separately as `agent_termination: "sigkill"` and is
NOT a confirmed timeout. A later successful write to the target invalidates
an earlier read even if that later write is not verified; the verified-write
requirement is independent.

**Testing:** `python3 -m unittest discover -s tests -v` runs the loader (8),
grader (34), and stubbed integration (3) suites. The integration test
replaces `podman`/`hermes` with fakes and does NOT validate real Podman or
Hermes execution or real inference.

**Known limitations:** monotonic timing, the SIGKILL-vs-timeout distinction,
and limit validation are only as good as the stub evidence; they are
unvalidated against a real container until the authorized real run.

## Regression evidence

An earlier raw run is retained locally (not committed). It omitted the required final LF but claimed success. Preserve it; do not repair or overwrite it. The correct byte counts are 18 for `container-agent-ok` and 19 for `container-agent-ok` followed by LF.

## Verified components

- Rootless Podman works.
- The pinned image is present and launches.
- The worker uses an isolated `HERMES_HOME=/opt/data` mounted from a fresh per-run host directory.
- The `file` toolset and the `write_file` / `read_file` workflow work.
- LM Studio authentication and provider `lmstudio` work using `LM_API_KEY` and `LM_BASE_URL`.
- One-shot `hermes -z` invocation works.
- Usage output, isolated JSONL session export, host grading, and post-run container cleanup all work.

## Boundaries and limitations

The worker should never receive the normal desktop Hermes profile, broad home directory, host checkout, runtime sockets, container-management socket, hidden grader, or reference answer. Only its fresh run directory is mounted under `/opt/data`.

This protects the regular Hermes installation from ordinary agent mistakes; it is **not** an absolute security boundary. Known limitations include unrestricted egress, environment-delivered token exposure, worker-visible evidence in the same mounted test home, and a separate LM Studio server outside the container's CPU/RAM limits.

## Next tasks — in order

1. **Make tasks authoritative.** Extend/load task JSON so task ID, prompt, target path, exact expected bytes, and allowed tools come from one versioned fixture.
2. **Enforce tool policy.** Explicitly fail a task when the trace includes a prohibited tool or unsupported creation method.
3. **Harden trace parsing.** Malformed JSON, absent fields, or non-object arguments must yield a structured grader result, not a grader crash.
4. **Add no-model grader tests.** Use synthetic fixtures for: valid pass; missing LF; missing/malformed trace; prohibited tool call; agent timeout with usable evidence; export/log failure.
5. **Update README.** Link this handoff and document the canonical passing validation.
6. **Then expand evaluation tasks.** Compare local model configurations using retained evidence and clearly recorded settings.

## Evaluation record fields

For every future run, preserve the exact model ID, quantization, backend/provider, Hermes image revision, effective context capability/configuration, sampling, MTP or thinking mode, KV-cache configuration, GPU placement/offload, elapsed time, result, and evidence directory. Record unavailable settings explicitly as `unknown`.

Planned comparison buckets:

- Models that fit the 8 GB RTX 3070.
- Models that fit the 16 GB GPU.
- Models that fit within roughly 24 GB combined VRAM.

## Agent operating rules

1. Read this file, `README.md`, the task fixture, and the canonical `result.json` before proposing edits.
2. Inspect existing scripts before modifying them.
3. Preserve all retained evidence.
4. Prefer synthetic no-model tests when changing graders.
5. Announce slow, state-changing, or paid-inference actions before requesting that they be run.
6. Every terminal script for the user must show conspicuous `BEGIN OUTPUT TO COPY` and `END OUTPUT TO COPY` markers.
7. Do not claim model success from its prose; use host-side artifacts and structured grading.

---

### Confirmed responsive local configuration

GPU: RTX 3070, 8 GB VRAM
Display: moved to separate 16 GB GPU
Hermes context capability/configuration: 65K
KV cache: GPU-resident
K cache: Q4
V cache: Q4
Measured context processing: 1,020 tok/s
Measured generation: 39 tok/s

Prior configuration: 90K context with cache outside VRAM:
144 tok/s context processing and 17 tok/s generation.

Interpretation: the GPU-resident Q4 KV cache at 65K is the current
interactive baseline. Treat it as a tested configuration, not a universal
quality optimum; compare Q8 only if task results justify the additional VRAM.

---

### Broad discovery stress test

A broad read-only Hermes discovery prompt that exceeded 14 minutes without
completion at 90K context with cache outside VRAM completed in approximately
4 minutes after changing to 65K context with GPU-resident Q4 K/V cache.

Model summary mentioned a reachable local LM Studio endpoint,
skills discovery, possible local-model configuration,
and other environment observations. Those claims are leads requiring
independent verification; they are not canonical evaluator facts.

Conclusion: this configuration is viable for bounded interactive work, but
broad environment audits remain expensive and should be broken into small,
explicitly scoped read-only checks.

---

## Mellum smoke-test validation — 2026-10-10

Model ID: mellum2.1-12b-a2.5b-thinking
Evidence: runs/run.xGecy3nC
Task: exact-file-v1
Outcome: pass
All six grader checks passed.
Agent exit: 0
Trace export exit: 0
Container removed successfully.

Manually reported LM Studio configuration:
- Inference GPU: RTX 3070, 8 GB
- Context setting: 65K
- KV cache: GPU-resident
- K cache: Q4
- V cache: Q4
- Monitor connected to the separate 16 GB GPU

The final response agreed with the host-verified file contents.
This validates one narrow tool-use task, not general agent reliability.

---

## Real v2 Mellum validation — 2026-10-11

The first real run of the v2 path (`run_v2.sh` → `worker_v2.sh` →
`grade_v2.py`) completed:

- Task: exact-file-v2, task version 1
- Grader: grade_v2 2.0.0
- Model: mellum2.1-12b-a2.5b-thinking
- Agent-process duration: 23.248 s (monotonic clock); total workflow 27.18 s;
  session-reported 15.113 s (three separate measures)
- Agent exit: 0; trace export exit: 0
- All seven grading checks passed, including allowed_tools_respected
- Expected and actual bytes matched
- Container removed; no infrastructure errors
- Overall outcome: pass
- Evidence: runs/run.wj9KLui7

This is one real smoke-test pass on one narrow tool-use task. It is NOT
general model reliability. The monotonic timing, SIGKILL-vs-timeout
distinction, and limit validation are now exercised against a real container
for this one pass; broader validation across models and configurations is
still needed.

---
Results reporting and the webpage dashboard are implemented as separate utilities; timing, configuration capture, and automatic report generation are integrated. Here is the current roadmap.

## Already completed

- [x] Run Hermes in an isolated, pinned Podman container.
- [x] Connect the container to your LM Studio endpoint.
- [x] Export session traces and retain run evidence.
- [x] Independently grade exact file bytes and the write/read sequence.
- [x] Validate the packaged smoke test with Qwen and Mellum.
- [x] Generate sortable CSV summaries from saved results.
- [x] Create an offline HTML dashboard generator. It has been tested with synthetic data; you haven’t yet confirmed its output on your machine.
- [x] Create a handoff document for future sessions and agents. Local handoff exists at ~/hermes-eval/HANDOFF-v2.md.
- [x] Make task definitions authoritative (tasklib + tasks/exact-file-v2.json).
- [x] Enforce tool policy and harden trace parsing (grade_v2.py).
- [x] Add no-model grader tests (34 synthetic cases in tests/test_grader_v2.py).
- [x] Add v2 runner/worker with monotonic agent timing and limit validation (run_v2.sh, worker_v2.sh).
- [x] Add a stubbed no-inference integration test (tests/test_integration_v2.py; pipeline wiring only).
- [x] Update reporting/dashboard for task/grader versions and the three-way timing split.

## Remaining work

- Distinguish automatically observed settings from manually supplied ones; leave unavailable values as `unknown`.
- Optionally refresh reports automatically after a completed evaluation, and provide an easy-to-find dashboard entry point rather than requiring timestamped directories to be located manually.
- Keep configurations separate in comparisons — for example, Mellum with GPU-resident Q4 cache versus Mellum with cache in system RAM.

The CSV remains a behind-the-scenes export; the webpage is the normal interface.

## Expand beyond the smoke test

- [ ] Add file-editing tasks with independently checked outputs.
- [ ] Add search-and-retrieval tasks with known answers.
- [ ] Add small coding tasks graded by tests.
- [ ] Add multi-step tasks requiring several tools.
- [ ] Add controlled failure/recovery tasks.
- [ ] Repeat tasks across configurations and display consistency, not just one-off passes.

Only after this phase should we treat the dashboard as a meaningful comparison of agent capabilities rather than an integration-test record.

## Documentation and usability throughout

- [ ] Keep `HANDOFF-v2.md` synchronized with completed milestones and the next task.
- [ ] Update the README with exact run, report, and dashboard commands.
- [ ] Preserve the Qwen and Mellum passing runs and the earlier missing-newline regression.
- [ ] Include conspicuous output-copy markers in terminal commands.
- [ ] Avoid unnecessary inference, package installation, and changes to your working Hermes profile.

## Current status and next step

The task JSON is authoritative (tasklib + exact-file-v2), the grader is hardened (grade_v2), synthetic tests are in place, timing/reporting are integrated, and the first real v2 Mellum run passed (`runs/run.wj9KLui7`). The next step is broader real validation across models and configurations; each run requires explicit authorization.

---


# Hermes Agent Model Evaluation — Handoff

> **Read this first.** This document is the current local-project orientation for a new human or agent. Read it before changing scripts, rerunning inference, or deleting artifacts.

## Current status

**Project state:** Experimental local evaluation harness. Task loading (`tasklib` + `tasks/exact-file-v2.json`), tool-policy enforcement and hardened grading (`grade_v2.py`), synthetic no-model tests, v2 runner/worker integration (`run_v2.sh`/`worker_v2.sh`), timing/reporting, privacy-safe reporting, and evaluator provenance are complete. Real v2 Mellum validation on 2026-10-11: one pre-cleanup pass (`runs/run.wj9KLui7`) and a completed three-run post-cleanup pilot (`runs/run.5W8oobTG`, `runs/run.TQQj4VI3`, `runs/run.JHZ5yJ2U`) — narrow-task passes, not general model reliability. The private GitHub repository (`jc-gh25/hermes-agent-model-evaluation`, PRIVATE) holds the full history; the privacy-safe reporting and evaluator-provenance implementation is committed and pushed at `f90a9ac` ("Add privacy-safe reporting and evaluator provenance").

**Current goal:** The three-run post-cleanup `exact-file-v2` Mellum repeatability pilot is COMPLETE — all three runs passed the narrow task (structured details below). The next planned work is a reader-facing `README.md` rewrite. This document authorizes no new inference or implementation work; every real run still requires explicit approval.

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
├── model_eval_report.py       # Privacy-safe CSV reporter: count-only infrastructure errors, invalid_data handling, location-derived evidence IDs, provenance grouping
├── model_eval_dashboard.py    # Offline HTML dashboard generator (provenance-aware)
├── tasks/
│   ├── exact-file-v1.json     # v1 task fixture (legacy)
│   └── exact-file-v2.json     # v2 authoritative task fixture
├── tests/                     # No-model suites: loader, grader, integration (stubbed), report-privacy
└── runs/
    ├── run.YFQhuYf4/          # v1 canonical successful validation
    ├── run.xGecy3nC/          # v1 Mellum successful validation
    ├── run.wj9KLui7/          # v2 pre-cleanup Mellum pass
    ├── run.5W8oobTG/          # v2 post-cleanup pilot pass 1/3
    ├── run.TQQj4VI3/          # v2 post-cleanup pilot pass 2/3
    └── run.JHZ5yJ2U/          # v2 post-cleanup pilot pass 3/3
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

## v2 integration (Stage 2 — complete)

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

**Testing (recorded validation — not rerun this turn):** the last
reported no-model suite had **68 passing tests**: loader (8), grader
(34), integration (9), and report-privacy (17), run with
`python3 -m unittest discover -s tests -v`. The integration suite is
STUBBED — it replaces `podman`/`hermes` with fakes and does NOT
validate real Podman or Hermes execution or real inference. The
report-privacy suite covers count-only infrastructure-error exports,
`invalid_data` handling of malformed error fields, location-derived
evidence identifiers, and provenance grouping, all against synthetic
fixtures.

**Known limitations:** the normal path (monotonic agent-process timing
and limit validation) is exercised against a real container by the
retained pre-cleanup and post-cleanup passes. Timeout (exit 124) and SIGKILL (exit 137) handling
are NOT validated by a normal pass — those paths need their own
dedicated evidence.

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
8. **Absent inputs:** when required credentials or endpoint inputs (for example `LM_API_KEY` or `LM_BASE_URL`) are absent, stop and ask the operator how to supply them. Do not search config files, credential stores, or session history to recover them without explicit approval of the source. Masking values in output does not replace authorization to access them.

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

This pass was recorded on the **pre-cleanup** runner (before the
endpoint/model-input cleanup). It is one real smoke-test pass on one
narrow tool-use task — NOT general model reliability. It exercised the
normal path (monotonic agent-process timing and limit validation) against
a real container; it did NOT exercise timeout (exit 124) or SIGKILL
(exit 137) handling, which need their own dedicated evidence. The
cleaned-up runner was subsequently validated against the real endpoint
(see "Post-cleanup validation" below).

## Post-cleanup validation — 2026-10-11

The first run of the **cleaned-up** runner (required `LM_BASE_URL`,
explicit model argument, pre-flight validation) completed:

- Evidence: runs/run.5W8oobTG
- Code HEAD at execution: f0394baea85b4993beaa792c48fba5c6e21582f2
- Working tree at execution: only HANDOFF-v2.md modified (uncommitted documentation edit)
- Task: exact-file-v2, task version 1
- Grader: grade_v2 2.0.0
- Model: mellum2.1-12b-a2.5b-thinking
- Agent-process duration: 22.462 s (monotonic clock); total workflow 26.614 s;
  session-reported 7.718 s (three separate measures)
- Agent exit: 0; trace export exit: 0
- Termination: normal; no infrastructure errors; container removed
- All seven grading checks passed (agent_exit_zero, exact_bytes,
  write_tool_completed, read_after_write_completed, requested_model,
  expected_endpoint, allowed_tools_respected); expected and actual bytes matched
- Configuration settings recorded as unknown (model loading, context,
  sampling, GPU placement, and KV-cache settings were not changed)
- Overall outcome: pass

This validates **end-to-end operation with valid inputs** against real
Podman, LM Studio, export, grading, and container cleanup. It does NOT
validate rejection of missing/invalid inputs — that path was checked
separately by the earlier no-model tests (the runner exits before any
run directory or container is created). Timeout (exit 124) and SIGKILL
(exit 137) paths also remain unvalidated by this normal run; they need
their own dedicated evidence. This is one pass on one narrow tool-use
task, not general model reliability.

### Post-cleanup repeatability pilot — COMPLETE (2026-10-11)

The three-run post-cleanup `exact-file-v2` Mellum pilot is complete:
`runs/run.5W8oobTG`, `runs/run.TQQj4VI3`, and `runs/run.JHZ5yJ2U`.
All three passed the narrow task. Each is one pass on one narrow
tool-use task — not general model reliability. The values below are
read from each run's structured `result.json`; the three timing
measures are separate and not interchangeable.

| Run | Agent-process (monotonic) | Total workflow (host) | Session-reported | Agent exit | Grading checks | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| `runs/run.5W8oobTG` | 22.462 s | 26.614 s | 7.718 s | 0 | 7/7 passed | pass |
| `runs/run.TQQj4VI3` | 14.08 s | 18.318 s | 7.209 s | 0 | 7/7 passed | pass |
| `runs/run.JHZ5yJ2U` | 36.204 s | 40.451 s | 29.213 s | 0 | 7/7 passed | pass |

Common to all three: task `exact-file-v2` version 1, grader `grade_v2`
2.0.0, model `mellum2.1-12b-a2.5b-thinking`, provider `lmstudio`
(endpoint as recorded in each run's metadata), termination `normal`,
`infrastructure_errors: []`, expected and actual bytes matched, and all
seven grading checks true (`agent_exit_zero`, `exact_bytes`,
`write_tool_completed`, `read_after_write_completed`, `requested_model`,
`expected_endpoint`, `allowed_tools_respected`). Configuration settings
(quantization, sampling, MTP, GPU placement, context size, KV precision)
are recorded as `unknown`. Agent-process durations span 14.08–36.204 s:
outcomes were consistent while timings varied. Code revisions were
reported separately in the operator run reports:
`f0394baea85b4993beaa792c48fba5c6e21582f2` for `run.5W8oobTG`, and
`0dbfdb073bcb2c5001b2ab9c1ab61a7b3f0254bb` for `run.TQQj4VI3` and
`run.JHZ5yJ2U`. These revisions were not captured automatically in the
retained metadata/result provenance fields. The reporter therefore
continues to label these runs' code provenance unknown; do not retrofit
their evidence.

The normal path (monotonic agent-process timing and limit validation)
is exercised against a real container by these passes. Timeout (exit
124) and SIGKILL (exit 137) handling remain UNVALIDATED by any real
run — those paths still need their own dedicated evidence.

## Evaluator provenance and reporting privacy (committed and pushed at f90a9ac)

The implementation commit `f90a9ac` ("Add privacy-safe reporting and
evaluator provenance") changed exactly six files: `README.md`,
`model_eval_dashboard.py`, `model_eval_report.py`, `run_v2.sh`,
`tests/test_integration_v2.py`, and `tests/test_report_privacy_v2.py`
(new). Behavior:

- `run_v2.sh` records in `metadata.json` (which `grade_v2.py` copies
  through to `result.json`): `code_fingerprint_scheme` (`v1`);
  `code_fingerprint` — scheme `v1` is `sha256` over the sorted
  `(filename, sha256(content))` pairs of `run_v2.sh`, `worker_v2.sh`,
  `grade_v2.py`, `tasklib.py`, and the exact per-run task-snapshot
  bytes under the stable name `task_snapshot.json`, computed from
  actual file bytes so uncommitted edits are captured; `code_commit`
  (or `unknown` when Git information is unavailable); and
  `code_tree_dirty` (`true` / `false` / `unknown`). A fingerprint
  computation failure is recorded as `unknown` — never a crash and
  never a false fingerprint. Report/dashboard code, credentials,
  endpoints, environment dumps, and unrelated files are excluded.
- `model_eval_report.py` exports `infrastructure_errors` as a safe
  count only (in `runs.csv` and `summary.csv`); a legacy result with
  no `infrastructure_errors` field counts as zero. A present non-list
  value (including null) is invalid report input: the attempt is
  exported with outcome `invalid_data` and counted in `summary.csv`'s
  `invalid_data` column — unscored (not a pass, valid attempt,
  successful timed run, or in any success percentage or timing median),
  but visible in total attempts and the invalid-data count; it is not a
  model failure and not an infrastructure error. The raw malformed value
  is never exported (the per-run count shows `unknown`). Evidence
  identifiers derive from where each result actually lives
  (`runs/<run-id>`), never from metadata strings, so they stay valid when
  the project is moved. Raw infrastructure-error messages are never
  exported to any report, CSV, notes, or dashboard HTML; they remain
  only in the local `result.json`.
- Runs group by fingerprint scheme + fingerprint alongside the
  task/grader/model/configuration keys, so a documentation-only commit
  (same fingerprint) does not split an equivalent implementation, while a
  real code change (different fingerprint) does. Runs without a recorded
  fingerprint show `unknown provenance` and stay distinct from runs with a
  recorded fingerprint; provenance is never invented or inferred from
  timestamps.

**All six retained real runs predate provenance capture**
(`run.YFQhuYf4`, `run.xGecy3nC`, `run.wj9KLui7`, `run.5W8oobTG`,
`run.TQQj4VI3`, `run.JHZ5yJ2U`): `code_fingerprint`, `code_commit`,
and `code_tree_dirty` are absent from their `metadata.json` and
`result.json`, so their recorded code provenance is `unknown`. Do not
retrofit their metadata — legacy runs keep unknown provenance by design.
Runner provenance capture is covered by stubbed integration tests;
export validation and provenance grouping are covered by the 17-test
report-privacy suite using synthetic fixtures. No retained real run
exercises the provenance capture added in `f90a9ac`.

---
Results reporting and the webpage dashboard are implemented as separate, manually invoked utilities. Timing is integrated into the v2 run path; configuration metadata fields exist, but settings unavailable to the runner remain unknown. Automatic report generation is not integrated. Here is the current roadmap.

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
- [x] Set up the private GitHub repository (`jc-gh25/hermes-agent-model-evaluation`, PRIVATE) and complete the first upload.
- [x] Complete the three-run post-cleanup `exact-file-v2` Mellum repeatability pilot (`runs/run.5W8oobTG`, `runs/run.TQQj4VI3`, `runs/run.JHZ5yJ2U` — all passed the narrow task).
- [x] Commit and push privacy-safe reporting and evaluator provenance at `f90a9ac` (exactly six files; no run evidence, reports, or patch files uploaded).

## Remaining work

- Rewrite `README.md` as the reader-facing entry point (next planned work; documentation only — no new inference or implementation).
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

The task JSON is authoritative (tasklib + exact-file-v2), the grader is hardened (grade_v2), synthetic tests are in place, timing/reporting are integrated, privacy-safe reporting and evaluator provenance are committed and pushed at `f90a9ac`, the first real v2 Mellum run passed (`runs/run.wj9KLui7`, pre-cleanup runner), and the three-run post-cleanup pilot passed against the real endpoint (`runs/run.5W8oobTG`, `runs/run.TQQj4VI3`, `runs/run.JHZ5yJ2U`). The next planned work is a reader-facing `README.md` rewrite. No new inference or implementation work is authorized by this document; every real run still requires explicit approval.

---


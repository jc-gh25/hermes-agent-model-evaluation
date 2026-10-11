#!/usr/bin/env bash
set -u
umask 077
BASE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 3
MODEL=${1:-}
IMAGE='docker.io/nousresearch/hermes-agent@sha256:7c6c7debc9e8fe623ffd5afce1c80116e8cff4e818ef42eb649d43562139d140'
TASK_SNAPSHOT="$BASE/tasks/exact-file-v2.json"
# Overhead reserved inside the container budget, beyond the two step
# budgets, for agent termination (SIGKILL grace) and trace-export
# startup. Validated against the snapshot's container_seconds below.
OVERHEAD_SECONDS=20
printf '\n================================================================\nBEGIN OUTPUT TO COPY\n================================================================\n'
NAME="hermes-eval-v2-$(date +%s)-$$"
cleanup() { podman rm -f "$NAME" >/dev/null 2>&1 || true; }
finish() {
  code=$?
  cleanup
  printf '\n================================================================\nEND OUTPUT TO COPY (runner exit %s)\n================================================================\n' "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for cmd in podman python3 timeout tee mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing: $cmd"; exit 3; }
done
[[ -n ${LM_API_KEY:-} ]] || { echo 'LM_API_KEY missing; enter it privately first.'; exit 3; }
export LM_API_KEY
# Model selection is explicit: the caller passes the exact
# model ID as the first argument. No model ID is used as a
# default.
[[ -n "$MODEL" ]] || { echo 'MODEL missing; pass the exact model ID as the first argument.'; exit 3; }
# The LM Studio endpoint is a required per-run configuration
# input (your host-reachable address), never hardcoded. Validate
# it before creating a run directory or starting a container.
# Setting LM_BASE_URL here does not change your normal Hermes
# configuration; the runner reads it only for this run.
[[ -n ${LM_BASE_URL:-} ]] || { echo 'LM_BASE_URL missing; set it to your host-reachable LM Studio endpoint (e.g. http://<host>:1234/v1).'; exit 3; }
[[ "$LM_BASE_URL" =~ ^https?:// ]] || { echo 'LM_BASE_URL must start with http:// or https://'; exit 3; }
export LM_BASE_URL
podman image inspect "$IMAGE" >/dev/null 2>&1 || { echo 'Pinned image missing; no pull attempted.'; exit 3; }
[[ -f "$TASK_SNAPSHOT" ]] || { echo "Task snapshot missing: $TASK_SNAPSHOT"; exit 3; }
mkdir -p "$BASE/runs" || exit 3
RUN=$(mktemp -d "$BASE/runs/run.XXXXXXXX") || exit 3
mkdir -p "$RUN/worker-home/workspace" || exit 3
export TEST_MODEL="$MODEL"
# LM_BASE_URL is exported above (required, validated before run creation).

# Validate the authoritative task snapshot, then derive the metadata,
# prompt, and limits from the exact same snapshot bytes. The snapshot
# (task.json) and the grader stay host-only, outside the worker mount;
# only the prompt and the derived per-step limits are delivered in.
python3 - "$BASE" "$RUN" "$TASK_SNAPSHOT" "$MODEL" "$IMAGE" "$OVERHEAD_SECONDS" "$LM_BASE_URL" <<'PY'
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tasklib import TaskError, load_task

base, run_dir, snapshot_path, model, image, overhead, base_url = sys.argv[1:8]
run = Path(run_dir)
overhead = int(overhead)

try:
    task = load_task(snapshot_path)
except TaskError as exc:
    print(f"TASK VALIDATION ERROR: {exc}", file=sys.stderr)
    sys.exit(3)

# Snapshot the exact authoritative bytes host-only (never mounted).
snapshot_bytes = Path(snapshot_path).read_bytes()
(run / "task.json").write_bytes(snapshot_bytes)
fixture_hash = hashlib.sha256(snapshot_bytes).hexdigest()

# Derive the prompt and limits from the same bytes.
prompt = task["prompt"]
limits = task["limits"]
agent_seconds = limits["agent_seconds"]
export_seconds = limits["export_seconds"]
container_seconds = limits["container_seconds"]

# Validate limit consistency rather than silently ignoring a budget
# that cannot hold both steps plus termination and export overhead.
required = agent_seconds + export_seconds + overhead
if required > container_seconds:
    print(
        "LIMIT ERROR: agent_seconds + export_seconds + overhead "
        f"({agent_seconds}+{export_seconds}+{overhead}={required}) "
        f"exceeds container_seconds ({container_seconds})",
        file=sys.stderr,
    )
    sys.exit(3)

# --- code provenance, captured at run time (host-side) ---
# Git commit and dirty state are recorded separately from the
# content fingerprint. Unavailable Git information is recorded
# as "unknown", never a false "clean".
def _git(*args):
    try:
        proc = subprocess.run(
            ["git", "-C", base, *args],
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except Exception:
        return None

_commit = _git("rev-parse", "HEAD")
_porcelain = _git("status", "--porcelain")
code_commit = _commit if _commit else "unknown"
code_tree_dirty = (
    "unknown" if _porcelain is None
    else ("true" if _porcelain.strip() else "false")
)

# --- code fingerprint (scheme v1) ---
# sha256 over the sorted (filename, sha256(content)) of the
# evaluation scripts and the EXACT per-run task snapshot bytes
# -- the snapshot already copied into the run directory (task.json)
# and used for this run, NOT a later re-read of the source
# fixture. The snapshot input carries the stable, descriptive
# name "task_snapshot.json". Report/dashboard code, credentials,
# endpoints, environment dumps, and unrelated files are excluded.
# Computed from actual file bytes, so uncommitted edits are
# captured too. Any computation failure is recorded as "unknown"
# -- never a crash and never a false fingerprint.
code_fingerprint_scheme = "v1"
try:
    _fingerprint_inputs = sorted(
        [(name, (Path(base) / name).read_bytes())
         for name in ("run_v2.sh", "worker_v2.sh", "grade_v2.py", "tasklib.py")]
        + [("task_snapshot.json", snapshot_bytes)]
    )
    _fingerprint = hashlib.sha256()
    for _name, _content in _fingerprint_inputs:
        _fingerprint.update(_name.encode("utf-8"))
        _fingerprint.update(b"\x00")
        _fingerprint.update(hashlib.sha256(_content).hexdigest().encode("ascii"))
        _fingerprint.update(b"\n")
    code_fingerprint = _fingerprint.hexdigest()
except Exception:
    code_fingerprint = "unknown"

# Host-only run metadata, derived from the snapshot.
metadata = {
    "task": task["id"],
    "task_version": task["task_version"],
    "schema_version": task["schema_version"],
    "task_fixture_sha256": fixture_hash,
    "image": image,
    "requested_model": model,
    "provider": "lmstudio",
    "base_url": base_url,
    "started_at": time.time(),
    "code_fingerprint_scheme": code_fingerprint_scheme,
    "code_fingerprint": code_fingerprint,
    "code_commit": code_commit,
    "code_tree_dirty": code_tree_dirty,
    "settings": {k: "unknown" for k in [
        "quantization", "sampling", "mtp", "gpu_placement", "context_size", "kv_precision"
    ]},
    "limits": limits,
}
(run / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

# Derived per-step limits, host-only, for the runner's container budget.
(run / "derived.json").write_text(json.dumps({
    "agent_seconds": agent_seconds,
    "export_seconds": export_seconds,
    "container_seconds": container_seconds,
}), encoding="utf-8")

# Deliver the prompt into the mount; the answer bytes stay host-only.
(run / "worker-home" / "prompt.txt").write_text(prompt, encoding="utf-8")

print(f"TASK:{task['id']} v{task['task_version']} sha256:{fixture_hash[:12]}")
print(f"LIMITS:agent={agent_seconds}s export={export_seconds}s "
      f"container={container_seconds}s overhead={overhead}s")
PY
SETUP_EXIT=$?
[[ $SETUP_EXIT -eq 0 ]] || exit $SETUP_EXIT

# Read the validated, derived limits for the container budget and the
# worker environment. Passed as arguments (not string-interpolated)
# to avoid path-quoting issues.
AGENT_SECONDS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['agent_seconds'])" "$RUN/derived.json")
EXPORT_SECONDS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['export_seconds'])" "$RUN/derived.json")
CONTAINER_SECONDS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['container_seconds'])" "$RUN/derived.json")
export AGENT_SECONDS EXPORT_SECONDS

echo "Run: $RUN"
echo "Requested model: $MODEL"
echo "LM Studio endpoint: $LM_BASE_URL"

# The pinned image, isolated worker mount, provider, endpoint, resource
# limits, and cleanup strategy match the proven v1 invocation. The
# container budget is the snapshot's validated container_seconds.
timeout --kill-after=10s "${CONTAINER_SECONDS}s" podman run --rm --init --interactive \
 --name "$NAME" --pull=never --user 10000:10000 \
 --userns=keep-id:uid=10000,gid=10000 --entrypoint /bin/bash \
 --cpus=2 --memory=2g --memory-swap=2g --pids-limit=100 \
 --env LM_API_KEY --env LM_BASE_URL --env TEST_MODEL --env HERMES_HOME=/opt/data \
 --env AGENT_SECONDS --env EXPORT_SECONDS \
 -v "$RUN/worker-home:/opt/data:rw" "$IMAGE" -s \
 < "$BASE/worker_v2.sh" 2>&1 | tee "$RUN/combined_output.log"
STATUSES=("${PIPESTATUS[@]}")
cleanup
REMAINS=0
podman container exists "$NAME" && REMAINS=1

# Invoke the v2 grader, not the legacy grader. Exit 0 = evaluation
# completed (pass or fail); exit 3 = infrastructure error.
python3 "$BASE/grade_v2.py" "$RUN" "${STATUSES[0]}" "${STATUSES[1]}" "$REMAINS"
GRADE_EXIT=$?
echo "RUN_EXIT:${STATUSES[0]} TEE_EXIT:${STATUSES[1]} CONTAINER_REMAINS:$REMAINS"
echo "Result: $RUN/result.json"
echo 'Candidate failure is a valid evaluation result; infrastructure errors are separate.'
exit "$GRADE_EXIT"

#!/usr/bin/env bash
set -u
umask 077
BASE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 3
MODEL=${1:-}
IMAGE='docker.io/nousresearch/hermes-agent@sha256:7c6c7debc9e8fe623ffd5afce1c80116e8cff4e818ef42eb649d43562139d140'
printf '\n================================================================\nBEGIN OUTPUT TO COPY\n================================================================\n'
NAME="hermes-eval-$(date +%s)-$$"
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
mkdir -p "$BASE/runs" || exit 3
RUN=$(mktemp -d "$BASE/runs/run.XXXXXXXX") || exit 3
mkdir -p "$RUN/worker-home/workspace" || exit 3
export TEST_MODEL="$MODEL"
# LM_BASE_URL is exported above (required, validated before run creation).
python3 - "$RUN" "$IMAGE" "$MODEL" "$LM_BASE_URL" <<'PY'
import json, sys, time
from pathlib import Path
r=Path(sys.argv[1])
m={'task':'exact-file-v1','image':sys.argv[2],'requested_model':sys.argv[3],
'provider':'lmstudio','base_url':sys.argv[4],'started_at':time.time(),
'settings':{k:'unknown' for k in ['quantization','sampling','mtp','gpu_placement','context_size','kv_precision']},
'limits':{'cpus':2,'memory':'2g','pids':100,'container_seconds':300,'agent_seconds':240}}
(r/'metadata.json').write_text(json.dumps(m,indent=2))
PY
echo "Run: $RUN"
echo "Requested model: $MODEL"
timeout --kill-after=10s 300s podman run --rm --init --interactive \
 --name "$NAME" --pull=never --user 10000:10000 \
 --userns=keep-id:uid=10000,gid=10000 --entrypoint /bin/bash \
 --cpus=2 --memory=2g --memory-swap=2g --pids-limit=100 \
 --env LM_API_KEY --env LM_BASE_URL --env TEST_MODEL --env HERMES_HOME=/opt/data \
 -v "$RUN/worker-home:/opt/data:rw" "$IMAGE" -s \
 < "$BASE/worker.sh" 2>&1 | tee "$RUN/combined_output.log"
STATUSES=("${PIPESTATUS[@]}")
cleanup
REMAINS=0
podman container exists "$NAME" && REMAINS=1
python3 "$BASE/grade.py" "$RUN" "${STATUSES[0]}" "${STATUSES[1]}" "$REMAINS"
GRADE_EXIT=$?
echo "RUN_EXIT:${STATUSES[0]} TEE_EXIT:${STATUSES[1]} CONTAINER_REMAINS:$REMAINS"
echo "Result: $RUN/result.json"
echo 'Candidate failure is a valid evaluation result; infrastructure errors are separate.'
exit "$GRADE_EXIT"

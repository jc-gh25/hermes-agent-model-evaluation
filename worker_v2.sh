#!/usr/bin/env bash
set -u
umask 077
# DATA_ROOT defaults to the isolated container mount (/opt/data). The
# no-inference integration test overrides it to a host directory so the
# same worker logic runs outside a container. Inside the container this
# variable is unset and the real mount is used.
DATA_ROOT=${DATA_ROOT:-/opt/data}
cd "$DATA_ROOT/workspace" || exit 3
for cmd in hermes timeout python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing: $cmd"; exit 3; }
done
hermes --version
# The prompt and per-step time limits are derived from the authoritative
# task snapshot by the runner and delivered into the mount. The expected
# answer bytes remain host-only and are never mounted here.
PROMPT_FILE="$DATA_ROOT/prompt.txt"
[[ -s "$PROMPT_FILE" ]] || { echo "prompt file missing"; exit 3; }
AGENT_SECONDS=${AGENT_SECONDS:-240}
EXPORT_SECONDS=${EXPORT_SECONDS:-30}

# --- agent step: measured with a monotonic clock, exit status preserved ---
# Monotonic readings avoid wall-clock jumps; only their difference (the
# elapsed agent-process duration) is graded. Session-reported duration
# (inside the exported trace) and total workflow time (host-side) are
# measured separately and must not be conflated with this value.
python3 - "$PROMPT_FILE" "$AGENT_SECONDS" <<'PY'
import os
import subprocess
import sys
import time
from pathlib import Path

prompt_file = sys.argv[1]
agent_seconds = int(sys.argv[2])
prompt = Path(prompt_file).read_text(encoding="utf-8")
data_root = Path(os.environ.get("DATA_ROOT", "/opt/data"))
agent_cmd = [
    "timeout", "--kill-after=5s", f"{agent_seconds}s",
    "hermes", "-z", prompt,
    "--provider", "lmstudio",
    "-m", os.environ.get("TEST_MODEL", ""),
    "-t", "file", "--in", str(data_root / "workspace"),
    "--usage-file", str(data_root / "usage.json"),
]
t0 = time.monotonic()
agent_exit = subprocess.run(agent_cmd).returncode
t1 = time.monotonic()
(data_root / "agent_timing.txt").write_text(
    f"{t0:.6f}\n{t1:.6f}\n", encoding="utf-8"
)
(data_root / "agent_exit.txt").write_text(f"{agent_exit}\n", encoding="utf-8")
print(f"AGENT_EXIT:{agent_exit}")
print(f"AGENT_MONOTONIC_SECONDS:{t1 - t0:.3f}")
PY

# --- trace export: separate step, own budget, exit status preserved ---
# A failed export leaves no usable trace, so the run is an infrastructure
# error; the host grader makes that classification from export_exit.txt.
python3 - "$EXPORT_SECONDS" <<'PY'
import os
import subprocess
import sys
from pathlib import Path

export_seconds = int(sys.argv[1])
data_root = Path(os.environ.get("DATA_ROOT", "/opt/data"))
export_cmd = [
    "timeout", "--kill-after=5s", f"{export_seconds}s",
    "hermes", "sessions", "export", "--format", "jsonl",
    "--cwd", str(data_root / "workspace"), "--redact", "--yes",
    str(data_root / "session_trace.jsonl"),
]
export_exit = subprocess.run(export_cmd).returncode
(data_root / "export_exit.txt").write_text(f"{export_exit}\n", encoding="utf-8")
print(f"EXPORT_EXIT:{export_exit}")
sys.exit(0 if export_exit == 0 else 3)
PY
EXPORT_STEP_EXIT=$?

# The host grader, not this process, determines task success. Preserve
# the agent's recorded exit for grading; only a failed export aborts
# the worker here (no trace to grade).
if [[ $EXPORT_STEP_EXIT -ne 0 ]]; then
  exit 3
fi
exit 0

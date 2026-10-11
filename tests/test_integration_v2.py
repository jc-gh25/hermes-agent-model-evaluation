"""No-inference integration test for the v2 runner/worker/grader.

STUBBED: this test replaces `podman` and `hermes` with stub
executables. It does NOT validate actual Podman or Hermes execution,
container isolation, or real model inference. It verifies the
run_v2.sh -> worker_v2.sh -> grade_v2.py pipeline wiring: task
snapshot/metadata agreement, environment handoff, exit propagation,
grading invocation, and cleanup.

The real v2 scripts are copied into an isolated temporary project, so
the synthetic run directory is created OUTSIDE the real runs/ and the
report directory is never touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

TASK_SNAPSHOT = PROJECT / "tasks" / "exact-file-v2.json"

# The stub podman simulates the container by mapping the -v mount
# source to DATA_ROOT and running the worker script fed on stdin.
PODMAN_STUB = r'''#!/usr/bin/env python3
import os
import subprocess
import sys

args = sys.argv[1:]

if args[:2] == ["image", "inspect"]:
    sys.exit(0 if os.environ.get("STUB_IMAGE_MODE", "present") == "present" else 1)

if args[:2] == ["container", "exists"]:
    # --rm removes the container on exit; report it as gone.
    sys.exit(1)

if args[:2] == ["rm", "-f"]:
    marker = os.environ.get("STUB_CLEANUP_MARKER")
    if marker:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("cleaned\n")
    sys.exit(0)

if args[:1] == ["run"]:
    mount_src = None
    extra_env = {}
    script = sys.stdin.read()
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-v" and i + 1 < len(args):
            mount_src = args[i + 1].split(":")[0]
            i += 2
        elif a == "--env" and i + 1 < len(args):
            spec = args[i + 1]
            if "=" in spec:
                key, value = spec.split("=", 1)
            else:
                key, value = spec, os.environ.get(spec, "")
            extra_env[key] = value
            i += 2
        else:
            i += 1
    env = os.environ.copy()
    env.update(extra_env)
    # Map the container mount point to the host worker-home so the
    # worker's /opt/data paths resolve to the isolated host directory.
    env["DATA_ROOT"] = mount_src or "/opt/data"
    proc = subprocess.run(["bash", "-s"], input=script, env=env, text=True)
    sys.exit(proc.returncode)

sys.exit(0)
'''

# The stub hermes simulates the agent and the trace export.
HERMES_STUB = r'''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]

if args[:1] == ["--version"]:
    print("hermes stub vtest")
    sys.exit(0)

if args[:1] == ["-z"]:
    workspace = None
    for i, a in enumerate(args):
        if a == "--in" and i + 1 < len(args):
            workspace = args[i + 1]
    if workspace is None:
        sys.exit(3)
    mode = os.environ.get("STUB_AGENT_MODE", "pass")
    content = "container-agent-ok" if mode == "missing_lf" else "container-agent-ok\n"
    with open(os.path.join(workspace, "agent-proof.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    if mode == "agent_fail":
        sys.exit(7)
    sys.exit(0)

if args[:1] == ["sessions"]:
    out = args[-1]
    target = "/opt/data/workspace/agent-proof.txt"
    trace = {
        "id": "stub-session",
        "model": os.environ.get("TEST_MODEL", "test-model"),
        "billing_provider": "lmstudio",
        "billing_base_url": os.environ.get("LM_BASE_URL", "http://lmstudio.test/v1"),
        "timings": {"wall_clock_ms": 1000},
        "messages": [
            {"role": "user", "content": "stub user"},
            {"role": "assistant", "tool_calls": [
                {"id": "w1", "function": {"name": "write_file",
                 "arguments": json.dumps({"path": target, "content": "container-agent-ok\n"})}}]},
            {"role": "tool", "tool_call_id": "w1", "content": json.dumps({"verified": True})},
            {"role": "assistant", "tool_calls": [
                {"id": "r1", "function": {"name": "read_file",
                 "arguments": json.dumps({"path": target, "offset": 1, "limit": 2000})}}]},
            {"role": "tool", "tool_call_id": "r1",
             "content": json.dumps({"not_found": False, "verified": True,
                                    "content": "1|container-agent-ok"})},
        ],
    }
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(trace) + "\n")
    sys.exit(0)

sys.exit(0)
'''


class IntegrationV2Tests(unittest.TestCase):
    """STUBBED pipeline test; does not exercise real Podman/Hermes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="integration-v2-")
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Isolated project: copy the REAL v2 scripts and task snapshot.
        self.project = self.base / "project"
        (self.project / "tasks").mkdir(parents=True)
        for name in ("run_v2.sh", "worker_v2.sh", "grade_v2.py", "tasklib.py"):
            shutil.copyfile(PROJECT / name, self.project / name)
        shutil.copyfile(TASK_SNAPSHOT, self.project / "tasks" / "exact-file-v2.json")
        # Stub executables.
        self.bindir = self.base / "bin"
        self.bindir.mkdir()
        for name, body in (("podman", PODMAN_STUB), ("hermes", HERMES_STUB)):
            stub = self.bindir / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        self.cleanup_marker = self.base / "cleanup.marker"

    def run_v2(self, *, model="stub-model", agent_mode="pass", image_mode="present"):
        env = os.environ.copy()
        env["PATH"] = str(self.bindir) + os.pathsep + env.get("PATH", "")
        env["LM_API_KEY"] = "stub-token"
        env["LM_BASE_URL"] = "http://lmstudio.test/v1"
        env["STUB_AGENT_MODE"] = agent_mode
        env["STUB_IMAGE_MODE"] = image_mode
        env["STUB_CLEANUP_MARKER"] = str(self.cleanup_marker)
        return subprocess.run(
            ["bash", str(self.project / "run_v2.sh"), model],
            env=env, capture_output=True, text=True, timeout=120,
        )

    def run_dir(self):
        runs = sorted((self.project / "runs").glob("run.*"))
        self.assertEqual(len(runs), 1, f"expected one run dir, found {len(runs)}")
        return runs[0]

    def test_pass_pipeline(self):
        """STUBBED: full pass path through run_v2 -> worker_v2 -> grade_v2."""
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertIn("EVALUATION_OUTCOME: PASS", proc.stdout)
        self.assertIn("BEGIN OUTPUT TO COPY", proc.stdout)
        self.assertIn("END OUTPUT TO COPY", proc.stdout)

        run = self.run_dir()
        # Grading was invoked and wrote a structured result.
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "pass")
        self.assertEqual(result["task"], "exact-file-v2")
        self.assertEqual(result["task_version"], 1)
        self.assertEqual(result["grader_version"], "2.0.0")
        self.assertTrue(all(result["checks"].values()))
        self.assertFalse(result["agent_timeout"])
        self.assertEqual(result["agent_termination"], "normal")

        # Snapshot/metadata agreement: task.json is the exact snapshot
        # bytes and metadata records its hash and identity.
        snapshot_bytes = TASK_SNAPSHOT.read_bytes()
        self.assertEqual((run / "task.json").read_bytes(), snapshot_bytes)
        import hashlib
        metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["task"], "exact-file-v2")
        self.assertEqual(metadata["task_version"], 1)
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["task_fixture_sha256"], hashlib.sha256(snapshot_bytes).hexdigest())
        self.assertEqual(metadata["requested_model"], "stub-model")
        self.assertEqual(metadata["base_url"], "http://lmstudio.test/v1")
        self.assertEqual(metadata["limits"]["agent_seconds"], 240)
        self.assertEqual(metadata["limits"]["export_seconds"], 30)
        self.assertEqual(metadata["limits"]["container_seconds"], 300)

        # Environment handoff: the worker received the model and endpoint
        # (the stub recorded them in the exported trace).
        trace = json.loads((run / "worker-home" / "session_trace.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(trace["model"], "stub-model")
        self.assertEqual(trace["billing_base_url"], "http://lmstudio.test/v1")

        # Monotonic agent timing was recorded (two readings, non-negative delta).
        timing = (run / "worker-home" / "agent_timing.txt").read_text(encoding="utf-8").split()
        self.assertEqual(len(timing), 2)
        self.assertGreaterEqual(float(timing[1]) - float(timing[0]), 0.0)
        self.assertEqual((run / "worker-home" / "agent_exit.txt").read_text(encoding="utf-8").strip(), "0")
        self.assertEqual((run / "worker-home" / "export_exit.txt").read_text(encoding="utf-8").strip(), "0")

        # Exit propagation and cleanup ran.
        self.assertTrue(self.cleanup_marker.is_file())
        self.assertTrue((run / "combined_output.log").is_file())
        # The synthetic run stayed inside the isolated project, not real runs/.
        self.assertNotIn("hermes-eval", str(run).replace(str(self.project), ""))

    def test_missing_lf_is_candidate_failure_not_infrastructure(self):
        """STUBBED: wrong bytes are a candidate fail (exit 0), not infra."""
        proc = self.run_v2(model="stub-model", agent_mode="missing_lf")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertIn("EVALUATION_OUTCOME: FAIL", proc.stdout)
        run = self.run_dir()
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "fail")
        self.assertFalse(result["checks"]["exact_bytes"])
        self.assertTrue(result["checks"]["write_tool_completed"])
        self.assertTrue(result["checks"]["read_after_write_completed"])
        self.assertEqual(result["infrastructure_errors"], [])

    def test_missing_image_is_infrastructure_error(self):
        """STUBBED: a missing pinned image aborts before any run (exit 3)."""
        proc = self.run_v2(model="stub-model", image_mode="missing")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("Pinned image missing", proc.stdout)
        # No run directory was created.
        runs_dir = self.project / "runs"
        run_dirs = list(runs_dir.glob("run.*")) if runs_dir.exists() else []
        self.assertEqual(run_dirs, [])


if __name__ == "__main__":
    unittest.main()

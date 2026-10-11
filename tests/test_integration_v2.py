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
        # Keep git from walking up out of the isolated project when
        # the runner records code provenance.
        env["GIT_CEILING_DIRECTORIES"] = str(self.project)
        return subprocess.run(
            ["bash", str(self.project / "run_v2.sh"), model],
            env=env, capture_output=True, text=True, timeout=120,
        )

    def run_dir(self):
        runs = sorted((self.project / "runs").glob("run.*"))
        self.assertEqual(len(runs), 1, f"expected one run dir, found {len(runs)}")
        return runs[0]

    def _run_dirs(self):
        return sorted((self.project / "runs").glob("run.*"),
                      key=lambda p: p.stat().st_mtime)

    def _metadata(self, run_dir):
        return json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            capture_output=True, text=True, check=True,
        )

    def _init_clean_repo(self):
        """Turn the isolated project copy into a clean git repo.

        A .gitignore keeps the synthetic run/report evidence from
        making the tree dirty once a run directory is created.
        """
        (self.project / ".gitignore").write_text(
            "runs/\nreports/\n__pycache__/\n*.pyc\n",
            encoding="utf-8",
        )
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        self._git("add", ".gitignore", "run_v2.sh", "worker_v2.sh",
                  "grade_v2.py", "tasklib.py", "tasks/exact-file-v2.json")
        self._git("commit", "-m", "test baseline")

    def _expected_fingerprint(self, snapshot_bytes):
        """Recompute the scheme-v1 fingerprint exactly as the
        runner does: over the four evaluation scripts plus the
        given snapshot bytes under the stable name
        task_snapshot.json."""
        import hashlib
        inputs = sorted(
            [(name, (self.project / name).read_bytes())
             for name in ("run_v2.sh", "worker_v2.sh",
                          "grade_v2.py", "tasklib.py")]
            + [("task_snapshot.json", snapshot_bytes)]
        )
        h = hashlib.sha256()
        for name, content in inputs:
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(hashlib.sha256(content).hexdigest().encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()

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

    # --- runner code-provenance capture (stubbed, no inference) ---
    def test_provenance_unavailable_git_unknown(self):
        """STUBBED: when Git information is unavailable (the isolated
        project is not a git repo), commit and dirty state are
        'unknown' -- never a false 'clean' -- while the content
        fingerprint is still computed."""
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        meta = self._metadata(self.run_dir())
        self.assertEqual(meta["code_commit"], "unknown")
        self.assertEqual(meta["code_tree_dirty"], "unknown")
        self.assertEqual(meta["code_fingerprint_scheme"], "v1")
        self.assertRegex(meta["code_fingerprint"], r"^[0-9a-f]{64}$")

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_provenance_clean_repo_recorded(self):
        """STUBBED: a clean git repo records the commit hash, a clean
        tree, and a valid scheme-v1 fingerprint."""
        self._init_clean_repo()
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        meta = self._metadata(self.run_dir())
        self.assertRegex(meta["code_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(meta["code_tree_dirty"], "false")
        self.assertEqual(meta["code_fingerprint_scheme"], "v1")
        self.assertRegex(meta["code_fingerprint"], r"^[0-9a-f]{64}$")

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_provenance_dirty_tree_recorded_and_fingerprint_changes(self):
        """STUBBED: a dirty tree is recorded as dirty (not falsely
        clean), the commit is unchanged, and the content fingerprint
        changes because it is computed from actual file bytes."""
        self._init_clean_repo()
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        first = set(self._run_dirs())
        self.assertEqual(len(first), 1)
        clean_meta = self._metadata(next(iter(first)))
        self.assertEqual(clean_meta["code_tree_dirty"], "false")
        clean_fp = clean_meta["code_fingerprint"]
        # Dirty the worker script (a trailing comment is valid bash).
        worker = self.project / "worker_v2.sh"
        worker.write_text(
            worker.read_text(encoding="utf-8") + "\n# dirty marker\n",
            encoding="utf-8",
        )
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        new_runs = set(self._run_dirs()) - first
        self.assertEqual(len(new_runs), 1)
        dirty_meta = self._metadata(next(iter(new_runs)))
        self.assertEqual(dirty_meta["code_tree_dirty"], "true")
        self.assertEqual(dirty_meta["code_commit"], clean_meta["code_commit"])
        self.assertNotEqual(dirty_meta["code_fingerprint"], clean_fp)
        self.assertRegex(dirty_meta["code_fingerprint"], r"^[0-9a-f]{64}$")

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_provenance_fingerprint_uses_snapshot_artifact(self):
        """STUBBED: the recorded fingerprint is computed from the
        exact per-run task snapshot artifact (the run dir's
        task.json bytes) under the stable name task_snapshot.json,
        not a re-read of the source fixture path."""
        self._init_clean_repo()
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        run_dir = self.run_dir()
        snapshot_bytes = (run_dir / "task.json").read_bytes()
        meta = self._metadata(run_dir)
        self.assertEqual(meta["code_fingerprint_scheme"], "v1")
        self.assertEqual(meta["code_fingerprint"],
                         self._expected_fingerprint(snapshot_bytes))

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_provenance_fingerprint_tracks_snapshot_mutation(self):
        """STUBBED: after the source fixture is mutated (post
        snapshot creation), a new run's fingerprint reflects the
        mutated snapshot bytes actually copied for that run, and
        differs from the earlier run's fingerprint."""
        self._init_clean_repo()
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        first = set(self._run_dirs())
        self.assertEqual(len(first), 1)
        first_dir = next(iter(first))
        first_fp = self._metadata(first_dir)["code_fingerprint"]
        first_snapshot = (first_dir / "task.json").read_bytes()
        # Mutate the source fixture (a valid task, different prompt).
        snapshot_path = self.project / "tasks" / "exact-file-v2.json"
        task = json.loads(snapshot_path.read_text(encoding="utf-8"))
        task["prompt"] = task["prompt"] + "\n# mutated for test\n"
        snapshot_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
        proc = self.run_v2(model="stub-model")
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        new_runs = set(self._run_dirs()) - first
        self.assertEqual(len(new_runs), 1)
        second_dir = next(iter(new_runs))
        second_snapshot = (second_dir / "task.json").read_bytes()
        # The second run used the mutated snapshot bytes.
        self.assertNotEqual(second_snapshot, first_snapshot)
        second_meta = self._metadata(second_dir)
        self.assertEqual(second_meta["code_fingerprint"],
                         self._expected_fingerprint(second_snapshot))
        self.assertNotEqual(second_meta["code_fingerprint"], first_fp)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_provenance_fingerprint_failure_is_unknown(self):
        """STUBBED: when a hashed script is unreadable, the
        fingerprint computation fails safely as 'unknown' -- the
        setup heredoc writes metadata.json instead of crashing,
        and no false fingerprint is recorded."""
        self._init_clean_repo()
        # Remove a hashed script that the setup heredoc does not
        # import, so only the fingerprint read fails.
        (self.project / "worker_v2.sh").unlink()
        proc = self.run_v2(model="stub-model")
        # The run may fail later (the container step needs the
        # worker script), but the setup phase must have survived.
        run_dirs = self._run_dirs()
        self.assertEqual(len(run_dirs), 1)
        meta = self._metadata(run_dirs[0])
        self.assertEqual(meta["code_fingerprint"], "unknown")
        self.assertEqual(meta["code_fingerprint_scheme"], "v1")
        # No Python traceback from the setup phase.
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()

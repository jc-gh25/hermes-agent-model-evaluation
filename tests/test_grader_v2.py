"""Standard-library-only tests for grade_v2 (Stage 1B).

Builds throwaway run directories in temp directories OUTSIDE runs/ so
synthetic fixtures cannot contaminate the real results dashboard. No model
inference is involved.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import grade_v2  # noqa: E402
from tasklib import load_task  # noqa: E402

TASK_PATH = PROJECT / "tasks" / "exact-file-v2.json"
TRACE_FIXTURE = PROJECT / "tests" / "fixtures" / "minimal_trace.jsonl"
TARGET = "/opt/data/workspace/agent-proof.txt"


def make_call(call_id, name, arguments):
    return {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }


def make_result(call_id, payload):
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}


def make_session(messages, *, model="test-model", base_url="http://lmstudio.test/v1",
                 timings=None, session_id="synthetic-0001"):
    return {
        "id": session_id,
        "model": model,
        "billing_provider": "lmstudio",
        "billing_base_url": base_url,
        "timings": timings if timings is not None else {"wall_clock_ms": 11051},
        "messages": messages,
    }


def trace_of(session):
    return json.dumps(session) + "\n"


def valid_trace():
    """A well-formed write-then-read trajectory with correct content."""
    return trace_of(make_session([
        {"role": "user", "content": "SANITIZED USER MESSAGE"},
        make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
        make_result("w1", {"verified": True}),
        make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
        make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
        {"role": "assistant", "content": "SANITIZED ASSISTANT MESSAGE"},
    ]))


def build_run(base, *, task_path=None, trace=None, proof_bytes=b"container-agent-ok\n",
              agent_exit=0, export_exit=0, run_exit=0, tee_exit=0, remains=0,
              requested_model="test-model", base_url="http://lmstudio.test/v1",
              with_agent_timing=True, with_metadata=True, with_task_snapshot=True,
              hash_mismatch=False, meta_task=None, meta_task_version=None):
    """Create a synthetic run directory; return (run_dir, (run_exit, tee_exit, remains))."""
    run = base / f"run.synthetic.{uuid.uuid4().hex[:8]}"
    (run / "worker-home" / "workspace").mkdir(parents=True)
    src_task = task_path or TASK_PATH
    task_obj = load_task(src_task)
    if with_task_snapshot:
        shutil.copyfile(src_task, run / "task.json")
    if with_metadata:
        snapshot_bytes = (run / "task.json").read_bytes() if with_task_snapshot else Path(src_task).read_bytes()
        fixture_hash = hashlib.sha256(snapshot_bytes).hexdigest()
        if hash_mismatch:
            fixture_hash = "0" * 64
        meta = {
            "task": meta_task if meta_task is not None else task_obj["id"],
            "task_version": meta_task_version if meta_task_version is not None else task_obj["task_version"],
            "schema_version": task_obj["schema_version"],
            "task_fixture_sha256": fixture_hash,
            "requested_model": requested_model,
            "base_url": base_url,
            "provider": "lmstudio",
            "image": "test-image@sha256:deadbeef",
            "started_at": 1_800_000_000.0,
            "settings": {},
            "limits": task_obj["limits"],
        }
        (run / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if proof_bytes is not None:
        (run / "worker-home" / "workspace" / "agent-proof.txt").write_bytes(proof_bytes)
    if trace is not None:
        (run / "worker-home" / "session_trace.jsonl").write_text(trace, encoding="utf-8")
    elif TRACE_FIXTURE.is_file():
        shutil.copyfile(TRACE_FIXTURE, run / "worker-home" / "session_trace.jsonl")
    if with_agent_timing:
        (run / "worker-home" / "agent_timing.txt").write_text("1800000000.0\n1800000011.0\n", encoding="utf-8")
    (run / "worker-home" / "agent_exit.txt").write_text(f"{agent_exit}\n", encoding="utf-8")
    (run / "worker-home" / "export_exit.txt").write_text(f"{export_exit}\n", encoding="utf-8")
    return run, (run_exit, tee_exit, remains)


class GraderV2Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="grader-v2-test-")
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def grade(self, **kwargs):
        run, statuses = build_run(self.base, **kwargs)
        return grade_v2.grade_run(run, *statuses)

    # --- valid / byte-comparison cases ---
    def test_valid_pass(self):
        res = self.grade(trace=valid_trace())
        self.assertEqual(res["outcome"], "pass")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertTrue(all(res["checks"].values()))
        self.assertFalse(res["agent_timeout"])
        self.assertEqual(res["task_version"], 1)
        self.assertEqual(res["task_schema_version"], 1)

    def test_missing_trailing_lf(self):
        res = self.grade(trace=valid_trace(), proof_bytes=b"container-agent-ok")
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["exact_bytes"])
        self.assertTrue(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["read_after_write_completed"])
        self.assertIn("exact_bytes", " ".join(res["failure_reasons"]).lower())

    def test_missing_proof_file(self):
        res = self.grade(trace=valid_trace(), proof_bytes=None)
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["exact_bytes"])
        self.assertIsNone(res["actual_hex"])

    # --- read/write ordering cases ---
    def test_read_before_write(self):
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["read_after_write_completed"])
        self.assertTrue(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["exact_bytes"])
        self.assertIn("read_after_write_completed", " ".join(res["failure_reasons"]))

    def test_write_read_write_without_final_read(self):
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
            make_call("w2", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w2", {"verified": True}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["read_after_write_completed"])
        self.assertTrue(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["exact_bytes"])

    def test_multiple_writes_then_final_read(self):
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
            make_call("w2", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w2", {"verified": True}),
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "pass")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertTrue(all(res["checks"].values()))

    def test_missing_required_calls(self):
        # Case A: only a read, no write -> write_tool_completed False.
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["write_tool_completed"])

        # Case B: only a write, no read -> read_after_write_completed False.
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["read_after_write_completed"])
        self.assertTrue(res["checks"]["write_tool_completed"])

    # --- tool policy / explicit failure cases ---
    def test_prohibited_tool(self):
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
            make_call("p1", "execute_code", {}),
            make_result("p1", {"success": True}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["allowed_tools_respected"])
        self.assertIn("execute_code", res["prohibited_tools"])
        self.assertTrue(any("prohibited tool calls" in fr for fr in res["failure_reasons"]))

    def test_explicit_tool_failure(self):
        # A write whose result reports success:false is a TASK failure,
        # not an infrastructure error.
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"success": False, "error": "disk full"}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["exact_bytes"])
        self.assertEqual(res["infrastructure_errors"], [])
        self.assertIn("write_tool_completed", " ".join(res["failure_reasons"]))

    # --- malformed evidence -> infrastructure_error (exit 3) ---
    def test_malformed_trace_json(self):
        res = self.grade(trace="{not json}\n")
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertIn("unusable session evidence", " ".join(res["infrastructure_errors"]))

    def test_malformed_messages_not_list(self):
        session = make_session([])
        session["messages"] = {}
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_malformed_non_object_message(self):
        session = make_session([])
        session["messages"] = ["oops"]
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_malformed_function_not_object(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            {"role": "assistant", "tool_calls": [{"id": "w1", "function": "not an object"}]},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_malformed_function_name_not_string(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            {"role": "assistant", "tool_calls": [{"id": "w1", "function": {"name": 123, "arguments": "{}"}}]},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_malformed_arguments_not_object(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            {"role": "assistant", "tool_calls": [{"id": "w1", "function": {"name": "write_file", "arguments": "[1,2]"}}]},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_malformed_result_content_not_object(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "x"}),
            {"role": "tool", "tool_call_id": "w1", "content": "plain text"},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_missing_call_id(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}]},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_missing_result_id(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "x"}),
            {"role": "tool", "content": "{}"},
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_duplicate_call_id(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "x"}),
            make_call("w1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_duplicate_result_id(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "x"}),
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("w1", {"verified": True}),
            make_result("w1", {"not_found": False}),
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)

    def test_result_before_call(self):
        session = make_session([
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_result("w1", {"verified": True}),
            make_call("w1", "write_file", {"path": TARGET, "content": "x"}),
        ])
        res = self.grade(trace=trace_of(session))
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("precedes its tool call" in e for e in res["infrastructure_errors"]))

    # --- missing/invalid run inputs -> infrastructure_error (exit 3) ---
    def test_missing_metadata(self):
        res = self.grade(trace=valid_trace(), with_metadata=False)
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("metadata.json" in e for e in res["infrastructure_errors"]))

    def test_missing_task_snapshot(self):
        res = self.grade(trace=valid_trace(), with_task_snapshot=False)
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("task snapshot" in e for e in res["infrastructure_errors"]))

    def test_hash_mismatch(self):
        res = self.grade(trace=valid_trace(), hash_mismatch=True)
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("hash mismatch" in e for e in res["infrastructure_errors"]))

    def test_task_identity_mismatch(self):
        res = self.grade(trace=valid_trace(), meta_task="wrong-id")
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("does not match snapshot" in e for e in res["infrastructure_errors"]))

    def test_task_version_mismatch(self):
        res = self.grade(trace=valid_trace(), meta_task_version=999)
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("task_version" in e for e in res["infrastructure_errors"]))

    # --- timeout / export / timing / model / endpoint ---
    def test_timeout_with_usable_evidence(self):
        res = self.grade(trace=valid_trace(), agent_exit=124)
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertTrue(res["agent_timeout"])
        self.assertEqual(res["agent_termination"], "timeout")
        self.assertTrue(res["checks"]["exact_bytes"])
        self.assertTrue(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["read_after_write_completed"])
        self.assertFalse(res["checks"]["agent_exit_zero"])

    def test_sigkill_not_timeout(self):
        # Exit 137 (SIGKILL) is NOT a confirmed timeout. It is
        # recorded separately; a well-formed trajectory remains a
        # task failure, not an infrastructure error.
        res = self.grade(trace=valid_trace(), agent_exit=137)
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["agent_timeout"])
        self.assertEqual(res["agent_termination"], "sigkill")
        self.assertFalse(res["checks"]["agent_exit_zero"])
        self.assertTrue(res["checks"]["exact_bytes"])
        self.assertTrue(res["checks"]["read_after_write_completed"])
        self.assertTrue(any("SIGKILL" in fr and "not a confirmed timeout" in fr
                            for fr in res["failure_reasons"]))

    def test_unverified_write_invalidates_earlier_read(self):
        # A later SUCCESSFUL but UNVERIFIED write still invalidates an
        # earlier read: read_after_write must be False. The verified-
        # write requirement is independent and still holds from the
        # earlier verified write.
        messages = [
            {"role": "user", "content": "SANITIZED USER MESSAGE"},
            make_call("w1", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w1", {"verified": True}),
            make_call("r1", "read_file", {"path": TARGET, "offset": 1, "limit": 2000}),
            make_result("r1", {"not_found": False, "verified": True, "content": "1|container-agent-ok"}),
            make_call("w2", "write_file", {"path": TARGET, "content": "container-agent-ok\n"}),
            make_result("w2", {"success": True}),
        ]
        res = self.grade(trace=trace_of(make_session(messages)))
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["read_after_write_completed"])
        self.assertTrue(res["checks"]["write_tool_completed"])
        self.assertTrue(res["checks"]["exact_bytes"])
        self.assertIn("read_after_write_completed", " ".join(res["failure_reasons"]))

    def test_export_failure(self):
        res = self.grade(trace=valid_trace(), export_exit=3)
        self.assertEqual(res["outcome"], "infrastructure_error")
        self.assertEqual(grade_v2.main_exit_code(res), 3)
        self.assertTrue(any("trace export exit" in e for e in res["infrastructure_errors"]))

    def test_missing_timing(self):
        res = self.grade(trace=valid_trace(), with_agent_timing=False)
        self.assertEqual(res["outcome"], "pass")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertEqual(res["metadata"]["agent_process_seconds"], "unknown")
        self.assertEqual(res["metadata"]["reported_session_seconds"], 11.051)
        self.assertIsInstance(res["metadata"]["wall_seconds"], (int, float))

    def test_model_mismatch(self):
        res = self.grade(trace=valid_trace(), requested_model="other-model")
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["requested_model"])
        self.assertIn("requested_model", " ".join(res["failure_reasons"]))

    def test_endpoint_mismatch(self):
        res = self.grade(trace=valid_trace(), base_url="http://other.test/v1")
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["expected_endpoint"])
        self.assertIn("expected_endpoint", " ".join(res["failure_reasons"]))

    # --- proof-file symlink escape ---
    def test_proof_symlink_escape(self):
        # A symlink at the proof path pointing OUTSIDE worker-home to a
        # file with the CORRECT content. If the grader followed the symlink
        # it would pass; it must refuse and report an exact_bytes failure.
        secret = self.base / "secret.txt"
        secret.write_bytes(b"container-agent-ok\n")
        run, statuses = build_run(self.base, trace=valid_trace())
        proof = run / "worker-home" / "workspace" / "agent-proof.txt"
        proof.unlink()
        proof.symlink_to(secret)
        res = grade_v2.grade_run(run, *statuses)
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(grade_v2.main_exit_code(res), 0)
        self.assertFalse(res["checks"]["exact_bytes"])
        self.assertIsNone(res["actual_hex"])


if __name__ == "__main__":
    unittest.main()

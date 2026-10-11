"""Standard-library-only tests for the task-definition loader (Stage 1A).

Builds throwaway fixture files in a temp directory; never touches runs/,
reports/, or the real task fixtures.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tasklib import TaskError, load_task  # noqa: E402

VALID = {
    "schema_version": 1,
    "id": "exact-file-v2",
    "task_version": 1,
    "prompt": (
        "Use write_file to create /opt/data/workspace/agent-proof.txt "
        "containing exactly the ASCII text container-agent-ok followed by "
        "exactly one newline (LF). Then use read_file to read that same "
        "file. Do not use a shell, code execution, or another method to "
        "create the file. Report whether the read-back matches."
    ),
    "target_path": "/opt/data/workspace/agent-proof.txt",
    "expected_utf8": "container-agent-ok\n",
    "allowed_tools": ["write_file", "read_file"],
    "limits": {
        "agent_seconds": 240,
        "export_seconds": 30,
        "container_seconds": 300,
    },
}


def clone():
    """Return a deep copy of VALID so tests can mutate safely."""
    return json.loads(json.dumps(VALID))


class TasklibTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tasklib-test-")
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, data):
        """Write data (str or JSON-serializable) to a temp task.json."""
        p = self.base / "task.json"
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_valid_fixture(self):
        t = load_task(self.write(clone()))
        self.assertEqual(t["id"], "exact-file-v2")
        self.assertEqual(t["task_version"], 1)
        self.assertEqual(t["schema_version"], 1)
        self.assertEqual(t["target_path"], "/opt/data/workspace/agent-proof.txt")
        self.assertEqual(t["allowed_tools"], ["write_file", "read_file"])
        self.assertEqual(
            t["limits"],
            {"agent_seconds": 240, "export_seconds": 30, "container_seconds": 300},
        )
        # Expected content preserves exactly one trailing LF.
        self.assertEqual(t["expected_utf8"], "container-agent-ok\n")
        self.assertEqual(t["expected_utf8"].encode("utf-8"), b"container-agent-ok\n")

    def test_invalid_json(self):
        with self.assertRaises(TaskError):
            load_task(self.write("{not valid json"))

    def test_missing_required_fields(self):
        for field in (
            "id", "prompt", "target_path", "expected_utf8",
            "schema_version", "task_version", "allowed_tools", "limits",
        ):
            d = clone()
            del d[field]
            with self.assertRaises(TaskError):
                load_task(self.write(d))

    def test_incorrect_field_types(self):
        for field in ("id", "prompt", "target_path", "expected_utf8"):
            d = clone()
            d[field] = 123
            with self.assertRaises(TaskError):
                load_task(self.write(d))
        d = clone()
        d["allowed_tools"] = "write_file"
        with self.assertRaises(TaskError):
            load_task(self.write(d))
        d = clone()
        d["limits"] = [1, 2, 3]
        with self.assertRaises(TaskError):
            load_task(self.write(d))
        with self.assertRaises(TaskError):
            load_task(self.write([1, 2, 3]))  # top-level not an object

    def test_boolean_zero_negative_version_or_limit(self):
        for field in ("schema_version", "task_version"):
            for bad in (True, False, 0, -1):
                d = clone()
                d[field] = bad
                with self.assertRaises(TaskError):
                    load_task(self.write(d))
        for key in ("agent_seconds", "export_seconds", "container_seconds"):
            for bad in (True, False, 0, -5, 1.5):
                d = clone()
                d["limits"][key] = bad
                with self.assertRaises(TaskError):
                    load_task(self.write(d))

    def test_empty_prompt_or_tool_names(self):
        d = clone()
        d["prompt"] = ""
        with self.assertRaises(TaskError):
            load_task(self.write(d))
        d = clone()
        d["allowed_tools"] = ["write_file", ""]
        with self.assertRaises(TaskError):
            load_task(self.write(d))
        d = clone()
        d["allowed_tools"] = []
        with self.assertRaises(TaskError):
            load_task(self.write(d))

    def test_duplicate_allowed_tools(self):
        d = clone()
        d["allowed_tools"] = ["write_file", "read_file", "write_file"]
        with self.assertRaises(TaskError):
            load_task(self.write(d))

    def test_invalid_target_paths(self):
        bad_paths = [
            "/opt/data/workspace",                        # directory itself
            "/opt/data/workspace/",                       # directory itself (trailing slash)
            "/opt/data/workspace/../workspace/agent-proof.txt",  # '..' traversal
            "/opt/data/workspace/../../etc/passwd",       # '..' escape
            "/etc/passwd",                                # not beneath workspace
            "workspace/agent-proof.txt",                  # relative, not absolute
            "/opt/data/other/agent-proof.txt",            # beneath /opt/data but not workspace
        ]
        for bad in bad_paths:
            d = clone()
            d["target_path"] = bad
            with self.assertRaises(TaskError):
                load_task(self.write(d))


if __name__ == "__main__":
    unittest.main()

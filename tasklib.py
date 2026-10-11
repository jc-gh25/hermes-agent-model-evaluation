"""Task-definition loader for the hermes-eval harness.

Standard-library only. This module is safe to import: it performs no CLI
parsing, no file writes, and no other execution at import time. The only
entry point that does work is guarded behind ``if __name__ == "__main__"``.

NOTE on path validation: ``_validate_target_path`` performs LEXICAL
validation only. It inspects the textual form of ``target_path``; it does
not touch the filesystem and is NOT a symlink/security boundary. Callers
that mount or resolve the target must enforce real filesystem isolation
separately.
"""

import json
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1

# Fields that must be present and nonempty strings.
_REQUIRED_STRING_FIELDS = ("id", "prompt", "target_path", "expected_utf8")

# Limit keys that must be present and positive integers.
_REQUIRED_LIMITS = ("agent_seconds", "export_seconds", "container_seconds")

# Workspace root for this initial fixture. Targets must be files beneath
# this directory (lexically).
WORKSPACE_ROOT = "/opt/data/workspace"


class TaskError(ValueError):
    """Raised when a task fixture is missing, malformed, or invalid."""


def _require_positive_int(value, label):
    """Raise TaskError unless value is a positive (non-bool) integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskError(f"{label} must be an integer")
    if value <= 0:
        raise TaskError(f"{label} must be a positive integer")


def _validate_target_path(target_path):
    """Lexically validate that target_path is a file beneath WORKSPACE_ROOT.

    Lexical validation only -- NOT a symlink/security boundary.
    """
    if not isinstance(target_path, str) or not target_path:
        raise TaskError("target_path must be a nonempty string")
    if not target_path.startswith(WORKSPACE_ROOT + "/"):
        raise TaskError(f"target_path must be a file beneath {WORKSPACE_ROOT}/")
    pure = PurePosixPath(target_path)
    if any(part == ".." for part in pure.parts):
        raise TaskError("target_path must not contain '..' traversal")
    if pure == PurePosixPath(WORKSPACE_ROOT):
        raise TaskError("target_path must not be the workspace directory itself")


def load_task(path):
    """Load and validate a task fixture.

    Returns the parsed task as a dict. Raises TaskError if the fixture is
    unreadable, malformed JSON, or fails any validation rule.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise TaskError(f"unreadable or invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskError("task fixture is not a JSON object")

    for field in _REQUIRED_STRING_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value:
            raise TaskError(f"field {field!r} must be a nonempty string")

    _require_positive_int(raw.get("schema_version"), "schema_version")
    _require_positive_int(raw.get("task_version"), "task_version")

    _validate_target_path(raw.get("target_path"))

    allowed = raw.get("allowed_tools")
    if not isinstance(allowed, list) or not allowed:
        raise TaskError("allowed_tools must be a nonempty list")
    seen = set()
    for tool in allowed:
        if not isinstance(tool, str) or not tool:
            raise TaskError("allowed_tools entries must be nonempty strings")
        if tool in seen:
            raise TaskError(f"duplicate allowed tool: {tool!r}")
        seen.add(tool)

    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise TaskError("limits must be an object")
    for key in _REQUIRED_LIMITS:
        _require_positive_int(limits.get(key), f"limits.{key}")

    return raw


def main(argv):
    """CLI entry point. Guarded behind __main__; not run at import time."""
    if len(argv) == 3 and argv[1] == "validate":
        print(json.dumps(load_task(argv[2]), indent=2))
        return 0
    print("usage: tasklib.py validate <task.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

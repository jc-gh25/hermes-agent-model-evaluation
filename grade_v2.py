"""Host-side grader (v2) for the hermes-eval harness.

This module is safe to import: it performs no CLI parsing, grading, or
writes at import time. ``grade_run`` evaluates a run directory and returns
a result dict without writing anything; ``write_result`` is the explicit,
separate step that persists it. The CLI lives behind ``__main__``.

Authoritative task: the per-run host-only task snapshot (``<run>/task.json``)
is loaded with ``tasklib.load_task``. Its recorded SHA-256 and task
identity/version (in ``<run>/metadata.json``) are validated against the
snapshot. The grader never falls back to hardcoded reference bytes: the
expected bytes come from the snapshot's ``expected_utf8``.

Grading: the final target-file bytes are read on the host and compared to
``expected_utf8`` encoded as UTF-8. Prohibited tools are rejected. A
successful write and a successful read after the LAST successful write of
the target are required. ``read_file`` display content (line-numbered) is
NOT compared to the reference bytes, and trailing LF is not inferred from
the tool's display.

Trace validation: messages, tool_calls, function objects, argument objects,
nonempty IDs, unique call IDs, unique result IDs, and result ordering are
validated. A result must follow its corresponding call. Malformed evidence
is a structured infrastructure error; an explicit tool-operation failure is
a task failure, not automatically an infrastructure error. A well-formed
incomplete trajectory (e.g. a timeout) is distinguished from malformed
evidence.

Outcomes: pass / fail / infrastructure_error, with the existing 0/3 CLI
exit contract (0 = evaluation completed, 3 = infrastructure error).

File handling: the container mount point /opt/data maps to the host
``<run>/worker-home``. Proof-file reads use this lexical mapping and do
NOT follow a symlink that resolves outside the synthetic worker workspace.
Missing, unreadable, or escaping proof paths are handled without crashing.
NOTE: this is NOT a complete malicious-worker security boundary; it is a
best-effort containment check for the synthetic evaluation context.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath

from tasklib import SCHEMA_VERSION, TaskError, load_task

# This grader's own version and the result-schema version it emits.
GRADER_VERSION = "2.0.0"
RESULT_SCHEMA_VERSION = 2

# Container mount point -> host mapping. /opt/data in the container is the
# host <run>/worker-home directory.
MOUNT_POINT = "/opt/data"

# Exit 124 is produced by the `timeout` wrapper when the agent exceeds
# its budget. Exit 137 (128 + SIGKILL) means the process was SIGKILLed,
# which could be the wrapper's kill-after, an OOM kill, or an external
# kill -- it is NOT by itself a confirmed timeout.
_TIMEOUT_EXIT = 124
_SIGKILL_EXIT = 137


class EvidenceError(ValueError):
    """Raised when run evidence is structurally unusable (infrastructure error)."""


def _decode(value):
    """Decode a possibly JSON-string-encoded trace field."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise EvidenceError(f"unreadable or invalid JSON: {exc}") from exc


def _validate_session(session):
    """Raise EvidenceError unless the trace session is structurally gradeable.

    Validates object shapes, nonempty/unique call and result IDs, and that
    every result follows its corresponding call. A call with no result is
    allowed: that is a well-formed incomplete trajectory (e.g. a timeout),
    not malformed evidence.
    """
    if not isinstance(session, dict):
        raise EvidenceError("session is not a JSON object")
    messages = session.get("messages")
    if not isinstance(messages, list):
        raise EvidenceError("session has no messages list")

    call_positions = {}
    result_records = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise EvidenceError(f"message at index {index} is not an object")
        role = message.get("role")
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if tool_calls is not None and not isinstance(tool_calls, list):
                raise EvidenceError(f"tool_calls at message {index} is not a list")
            for call in tool_calls or []:
                if not isinstance(call, dict):
                    raise EvidenceError(f"tool_call at message {index} is not an object")
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    raise EvidenceError(f"tool_call at message {index} has a missing/empty id")
                function = call.get("function")
                if not isinstance(function, dict):
                    raise EvidenceError(f"tool_call {call_id!r} function is not an object")
                name = function.get("name")
                if not isinstance(name, str) or not name:
                    raise EvidenceError(f"tool_call {call_id!r} function.name is missing/empty")
                try:
                    arguments = _decode(function.get("arguments"))
                except Exception as exc:
                    raise EvidenceError(f"tool_call {call_id!r} arguments are not valid JSON: {exc}") from exc
                if not isinstance(arguments, dict):
                    raise EvidenceError(f"tool_call {call_id!r} arguments are not a JSON object")
                if call_id in call_positions:
                    raise EvidenceError(f"duplicate tool call id: {call_id!r}")
                call_positions[call_id] = index
        elif role == "tool":
            result_id = message.get("tool_call_id")
            if not isinstance(result_id, str) or not result_id:
                raise EvidenceError(f"tool result at message {index} has a missing/empty tool_call_id")
            try:
                payload = _decode(message.get("content"))
            except Exception as exc:
                raise EvidenceError(f"tool result {result_id!r} content is not valid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise EvidenceError(f"tool result {result_id!r} content is not a JSON object")
            result_records.append((result_id, index))

    seen_results = set()
    for result_id, index in result_records:
        if result_id in seen_results:
            raise EvidenceError(f"duplicate tool result id: {result_id!r}")
        seen_results.add(result_id)
        if result_id not in call_positions:
            raise EvidenceError(f"tool result {result_id!r} has no matching tool call")
        if index < call_positions[result_id]:
            raise EvidenceError(f"tool result {result_id!r} precedes its tool call")


def _host_proof_path(home, target_path):
    """Map the container target path to a host path under the worker mount.

    Returns None if the target is not beneath the mount point.
    """
    target = PurePosixPath(target_path)
    try:
        rel = target.relative_to(MOUNT_POINT)
    except ValueError:
        return None
    return home.joinpath(*rel.parts)


def _read_proof_bytes(proof, home):
    """Read the proof file's bytes without following an escaping symlink.

    Returns None if the path is missing, unreadable, not a regular file, or
    a symlink (or path) that resolves outside the synthetic worker workspace
    (the host ``worker-home`` mount). This is a best-effort containment
    check, NOT a complete malicious-worker security boundary.
    """
    try:
        if not proof.is_file():
            return None
        real_proof = Path(os.path.realpath(proof))
        real_home = Path(os.path.realpath(home))
        try:
            real_proof.relative_to(real_home)
        except ValueError:
            # Escapes the worker workspace; do not follow/read it.
            return None
        return proof.read_bytes()
    except OSError:
        return None


def grade_run(run, run_exit, tee_exit, remains):
    """Grade one run directory. Returns a result dict; never raises on bad evidence.

    Required inputs under ``run``:
      - ``task.json``            host-only task snapshot (tasklib-validated)
      - ``metadata.json``        run metadata: task, task_version, schema_version,
                                 task_fixture_sha256, requested_model, base_url,
                                 provider, image, started_at, settings, limits
      - ``worker-home/agent_exit.txt``     agent process exit code
      - ``worker-home/export_exit.txt``    trace export exit code
      - ``worker-home/agent_timing.txt``   optional: two monotonic clock
                                 readings "<start>\n<end>" (worker_v2 writes
                                 time.monotonic values); their difference is
                                 the measured agent-process duration
      - ``worker-home/session_trace.jsonl``  exactly one JSON session per file
      - ``worker-home/...``      the target file at the mount-mapped path
    """
    run = Path(run)
    infra = []
    home = run / "worker-home"
    task = None
    meta = {}

    # --- metadata (may be missing/invalid) ---
    try:
        meta = _load_json(run / "metadata.json")
        if not isinstance(meta, dict):
            raise EvidenceError("metadata.json is not a JSON object")
    except EvidenceError as exc:
        meta = {}
        infra.append(f"missing or invalid metadata.json: {exc}")

    # --- authoritative task snapshot (host-only) ---
    try:
        task = load_task(run / "task.json")
    except TaskError as exc:
        task = None
        infra.append(f"missing or invalid task snapshot: {exc}")

    # --- validate recorded hash and task identity/version ---
    if task is not None and meta:
        recorded_hash = meta.get("task_fixture_sha256")
        if not isinstance(recorded_hash, str) or not recorded_hash:
            infra.append("metadata missing task_fixture_sha256")
        else:
            try:
                actual_hash = hashlib.sha256((run / "task.json").read_bytes()).hexdigest()
            except OSError as exc:
                infra.append(f"unreadable task snapshot for hashing: {exc}")
            else:
                if actual_hash != recorded_hash:
                    infra.append("task fixture hash mismatch")
        if meta.get("task") != task["id"]:
            infra.append(
                f"metadata task {meta.get('task')!r} does not match snapshot {task['id']!r}"
            )
        if meta.get("task_version") != task["task_version"]:
            infra.append(
                f"metadata task_version {meta.get('task_version')!r} does not match "
                f"snapshot {task['task_version']!r}"
            )

    # --- status files ---
    def status_file(name):
        try:
            return int((home / name).read_text(encoding="utf-8").strip())
        except Exception:
            infra.append(f"missing or invalid {name}")
            return None

    agent_exit = status_file("agent_exit.txt")
    export_exit = status_file("export_exit.txt")

    if run_exit:
        infra.append(f"container wrapper exit {run_exit}")
    if tee_exit:
        infra.append(f"log capture exit {tee_exit}")
    if remains:
        infra.append("container remains")
    if export_exit not in (None, 0):
        infra.append(f"trace export exit {export_exit}")

    # --- session trace ---
    session = None
    try:
        text = (home / "session_trace.jsonl").read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        sessions = [json.loads(line) for line in lines]
        if len(sessions) != 1:
            raise EvidenceError(f"expected exactly one session, found {len(sessions)}")
        session = sessions[0]
        _validate_session(session)
    except EvidenceError as exc:
        session = None
        infra.append(f"unusable session evidence: {exc}")
    except Exception as exc:
        session = None
        infra.append(f"unusable session evidence: {type(exc).__name__}: {exc}")

    # --- exact bytes (host-side, authoritative) ---
    expected_bytes = task["expected_utf8"].encode("utf-8") if task else None
    proof = _host_proof_path(home, task["target_path"]) if task else None
    actual = _read_proof_bytes(proof, home) if proof is not None else None

    checks = {
        "agent_exit_zero": agent_exit == 0,
        "exact_bytes": actual == expected_bytes if (task and expected_bytes is not None) else False,
        "write_tool_completed": False,
        "read_after_write_completed": False,
        "requested_model": False,
        "expected_endpoint": False,
        "allowed_tools_respected": True,
    }

    # Only exit 124 is a confirmed timeout from the wrapper. Exit 137
    # (SIGKILL) is recorded separately; stronger attribution needs
    # explicit evidence such as the wrapper's own timeout log.
    agent_timeout = agent_exit == _TIMEOUT_EXIT
    if agent_exit == _SIGKILL_EXIT:
        agent_termination = "sigkill"
    elif agent_timeout:
        agent_termination = "timeout"
    elif agent_exit == 0:
        agent_termination = "normal"
    elif agent_exit is None:
        agent_termination = "unknown"
    else:
        agent_termination = "error"
    prohibited_tools = []
    calls = []
    observed_model = observed_provider = session_id = None
    reported_session_seconds = "unknown"

    if session is not None:
        observed_model = session.get("model")
        observed_provider = session.get("billing_provider")
        session_id = session.get("id")
        if task and meta:
            checks["requested_model"] = observed_model == meta.get("requested_model")
            checks["expected_endpoint"] = session.get("billing_base_url") == meta.get("base_url")
        timings = session.get("timings")
        if isinstance(timings, dict):
            wall_ms = timings.get("wall_clock_ms")
            if isinstance(wall_ms, (int, float)) and not isinstance(wall_ms, bool) and wall_ms >= 0:
                reported_session_seconds = round(wall_ms / 1000, 3)

        messages = session["messages"]
        results = {}
        for index, message in enumerate(messages):
            if message.get("role") == "tool":
                results[message["tool_call_id"]] = (index, message)

        last_write_result_index = None
        target_str = task["target_path"] if task else None
        allowed = set(task["allowed_tools"]) if task else None

        for index, message in enumerate(messages):
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name")
                try:
                    arguments = _decode(function.get("arguments", {}))
                except Exception:
                    arguments = {}
                result_pair = results.get(call.get("id"))
                payload = None
                if result_pair is not None:
                    try:
                        payload = _decode(result_pair[1].get("content"))
                    except Exception:
                        pass
                completed = (
                    isinstance(payload, dict)
                    and not payload.get("error")
                    and payload.get("success") is not False
                )
                calls.append({
                    "name": name,
                    "arguments": arguments,
                    "has_tool_result": result_pair is not None,
                })
                if allowed is not None and isinstance(name, str) and name not in allowed:
                    if name not in prohibited_tools:
                        prohibited_tools.append(name)
                if name == "write_file" and target_str and arguments.get("path") == target_str:
                    if completed:
                        # Any successful write to the target invalidates an
                        # earlier read: a read after THIS write is now
                        # required, whether or not this write was verified.
                        last_write_result_index = result_pair[0]
                        checks["read_after_write_completed"] = False
                        if payload.get("verified") is True:
                            checks["write_tool_completed"] = True
                if (name == "read_file" and target_str and arguments.get("path") == target_str
                        and completed and not payload.get("not_found")
                        and isinstance(payload.get("content"), str)):
                    if last_write_result_index is not None and result_pair is not None:
                        if result_pair[0] > last_write_result_index:
                            checks["read_after_write_completed"] = True

        if prohibited_tools:
            checks["allowed_tools_respected"] = False

    # --- timing: three separately labeled measures ---
    # agent_timing.txt holds two monotonic clock readings (worker_v2);
    # their difference is the measured agent-process duration. Missing
    # or invalid timing is unknown, not a task failure.
    agent_process_seconds = "unknown"
    try:
        parts = (home / "agent_timing.txt").read_text(encoding="utf-8").split()
        if len(parts) >= 2:
            start, end = float(parts[0]), float(parts[1])
            if end >= start:
                agent_process_seconds = round(end - start, 3)
    except Exception:
        pass  # missing/invalid timing is unknown, not a task failure

    finished_at = time.time()
    started_at = meta.get("started_at")
    wall_seconds = "unknown"
    if isinstance(started_at, (int, float)) and not isinstance(started_at, bool):
        wall_seconds = round(finished_at - started_at, 3)

    meta["finished_at"] = finished_at
    meta["wall_seconds"] = wall_seconds
    meta["wall_seconds_meaning"] = "total_workflow"
    meta["agent_process_seconds"] = agent_process_seconds
    meta["reported_session_seconds"] = reported_session_seconds
    meta["observed_model"] = observed_model
    meta["observed_provider"] = observed_provider
    meta["session_id"] = session_id

    # --- assemble result ---
    failure_reasons = []
    if not checks["agent_exit_zero"]:
        if agent_exit is not None:
            failure_reasons.append(f"agent exited {agent_exit}")
        else:
            failure_reasons.append("agent exit status missing")
    if task:
        if not checks["exact_bytes"]:
            if actual is not None:
                failure_reasons.append(
                    f"exact_bytes: expected {len(expected_bytes)} bytes, got {len(actual)} bytes"
                )
            else:
                failure_reasons.append("exact_bytes: target file missing or unreadable")
        if not checks["write_tool_completed"]:
            failure_reasons.append(
                "write_tool_completed: no completed, verified write_file of the target"
            )
        if not checks["read_after_write_completed"]:
            failure_reasons.append(
                "read_after_write_completed: no completed read_file of the target "
                "after its last successful write"
            )
    if session is not None:
        if not checks["requested_model"]:
            failure_reasons.append(
                f"requested_model: session model {observed_model!r} does not match "
                f"requested {meta.get('requested_model')!r}"
            )
        if not checks["expected_endpoint"]:
            failure_reasons.append(
                f"expected_endpoint: session endpoint {session.get('billing_base_url')!r} "
                f"does not match expected {meta.get('base_url')!r}"
            )
    if not checks["allowed_tools_respected"]:
        failure_reasons.append(
            "allowed_tools_respected: prohibited tool calls: %s" % ", ".join(sorted(prohibited_tools))
        )
    if agent_timeout:
        failure_reasons.append(f"agent timed out (exit {agent_exit}) with usable evidence")
    elif agent_exit == _SIGKILL_EXIT:
        failure_reasons.append(
            f"agent terminated by SIGKILL (exit {agent_exit}); not a confirmed timeout"
        )

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task": task["id"] if task else meta.get("task", "unknown"),
        "task_version": task["task_version"] if task else "unknown",
        "task_schema_version": task["schema_version"] if task else "unknown",
        "grader_version": GRADER_VERSION,
        "outcome": "infrastructure_error" if infra else ("pass" if all(checks.values()) else "fail"),
        "checks": checks,
        "failure_reasons": failure_reasons,
        "agent_timeout": agent_timeout,
        "agent_termination": agent_termination,
        "agent_exit": agent_exit,
        "prohibited_tools": prohibited_tools,
        "infrastructure_errors": infra,
        "expected_hex": expected_bytes.hex() if expected_bytes is not None else None,
        "actual_hex": actual.hex() if actual is not None else None,
        "tool_calls": calls,
        "metadata": meta,
    }


def write_result(run, result):
    """Explicitly persist a result dict to ``<run>/result.json``.

    Kept separate from grade_run so evaluation logic never writes files.
    """
    (Path(run) / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main_exit_code(result):
    """Existing CLI exit contract: 0 = evaluation completed, 3 = infrastructure error."""
    return 3 if result["outcome"] == "infrastructure_error" else 0


def main(argv):
    """CLI entry point. Guarded behind __main__; not run at import time."""
    if len(argv) != 5:
        print("usage: grade_v2.py <run-dir> <run-exit> <tee-exit> <container-remains 0|1>",
              file=sys.stderr)
        return 2
    run = Path(argv[1])
    try:
        run_exit, tee_exit, remains = (int(v) for v in argv[2:5])
    except ValueError:
        print("grade_v2.py: run/tee/remains arguments must be integers", file=sys.stderr)
        return 2
    try:
        result = grade_run(run, run_exit, tee_exit, remains)
    except Exception as exc:
        # Last-resort: a grader crash must still leave a structured result.
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "task": "unknown",
            "task_version": "unknown",
            "task_schema_version": "unknown",
            "grader_version": GRADER_VERSION,
            "outcome": "infrastructure_error",
            "checks": {},
            "failure_reasons": [],
            "agent_timeout": False,
            "agent_exit": None,
            "prohibited_tools": [],
            "infrastructure_errors": [f"grader internal error: {type(exc).__name__}: {exc}"],
            "expected_hex": None,
            "actual_hex": None,
            "tool_calls": [],
            "metadata": {},
        }
    write_result(run, result)
    print("EVALUATION_OUTCOME:", result["outcome"].upper())
    for key, value in result.get("checks", {}).items():
        print(f"  {key}: {'PASS' if value else 'FAIL'}")
    for issue in result["infrastructure_errors"]:
        print("  infrastructure:", issue)
    for reason in result.get("failure_reasons", []):
        print("  reason:", reason)
    if result.get("agent_timeout"):
        print("  agent_timeout: True")
    if result.get("prohibited_tools"):
        print("  prohibited_tools:", ", ".join(result["prohibited_tools"]))
    print("EXPECTED_HEX:", result.get("expected_hex"))
    print("ACTUAL_HEX:", result.get("actual_hex"))
    print("Result written:", Path(run) / "result.json")
    return main_exit_code(result)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

import json
import sys
import time
from pathlib import Path

run = Path(sys.argv[1])
run_exit, tee_exit, remains = map(int, sys.argv[2:5])
home = run / "worker-home"
meta = json.loads((run / "metadata.json").read_text())
infra = []
def status_file(name):
    try:
        return int((home / name).read_text().strip())
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
try:
    sessions = [json.loads(line) for line in (home / "session_trace.jsonl").read_text().splitlines() if line.strip()]
    if len(sessions) != 1 or not isinstance(sessions[0].get("messages"), list):
        raise ValueError("expected one session containing messages")
except Exception as exc:
    sessions = []
    infra.append(f"unusable session evidence: {exc}")
expected = b"container-agent-ok\n"
proof = home / "workspace" / "agent-proof.txt"
actual = proof.read_bytes() if proof.is_file() else None
checks = {"agent_exit_zero": agent_exit == 0, "exact_bytes": actual == expected,
          "write_tool_completed": False, "read_after_write_completed": False,
          "requested_model": False, "expected_endpoint": False}
calls = []
def decode(value):
    return json.loads(value) if isinstance(value, str) else value
if sessions:
    session = sessions[0]
    checks["requested_model"] = session.get("model") == meta["requested_model"]
    checks["expected_endpoint"] = session.get("billing_base_url") == meta["base_url"]
    messages = session["messages"]
    results = {m.get("tool_call_id"): (i,m) for i,m in enumerate(messages) if m.get("role") == "tool"}
    write_result_index = None
    for i, message in enumerate(messages):
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                args = decode(function.get("arguments", {}))
            except Exception:
                args = {}
            result_pair = results.get(call.get("id"))
            payload = None
            if result_pair:
                try:
                    payload = decode(result_pair[1].get("content"))
                except Exception:
                    pass
            good = isinstance(payload, dict) and not payload.get("error") and payload.get("success") is not False
            calls.append({"name":name,"arguments":args,"has_tool_result":bool(result_pair)})
            path_ok = args.get("path") == "/opt/data/workspace/agent-proof.txt"
            if name == "write_file" and path_ok and good and payload.get("verified") is True:
                checks["write_tool_completed"] = True
                write_result_index = result_pair[0]
            if name == "read_file" and path_ok and good and not payload.get("not_found") and isinstance(payload.get("content"), str):
                if write_result_index is not None and i > write_result_index:
                    checks["read_after_write_completed"] = True
    meta["observed_model"] = session.get("model")
    meta["observed_provider"] = session.get("billing_provider")
    meta["session_id"] = session.get("id")
    meta["session_timings"] = session.get("timings")
meta["finished_at"] = time.time()
meta["wall_seconds"] = meta["finished_at"] - meta["started_at"]
result = {"task":meta["task"],"outcome":"infrastructure_error" if infra else ("pass" if all(checks.values()) else "fail"),
          "checks":checks,"infrastructure_errors":infra,"agent_exit":agent_exit,
          "expected_hex":expected.hex(),"actual_hex":actual.hex() if actual is not None else None,
          "tool_calls":calls,"metadata":meta}
(run / "result.json").write_text(json.dumps(result,indent=2))
print("EVALUATION_OUTCOME:", result["outcome"].upper())
for key, value in checks.items():
    print(f"  {key}: {'PASS' if value else 'FAIL'}")
for issue in infra:
    print("  infrastructure:", issue)
print("EXPECTED_BYTES:",repr(expected))
print("ACTUAL_BYTES:",repr(actual))
sys.exit(3 if infra else 0)

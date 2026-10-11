#!/usr/bin/env python3
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

BAR = "=" * 72

def _optional_float(value):
    """Return a non-negative float, or None when absent/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None

def _version_field(result, key):
    """Return a version string, or 'legacy' when the field is absent.

    Existing (pre-versioning) results do not carry task_version,
    grader_version, or schema_version. They are labelled 'legacy'
    so they are never mistaken for, or assigned, the new version.
    """
    value = result.get(key)
    if value is None or value == "":
        return "legacy"
    return str(value)

def main():
    base = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "hermes-eval"
    rows, warnings = [], []
    for p in sorted((base / "runs").glob("*/result.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            m = r.get("metadata") or {}
            settings = m.get("settings") or {}
            workflow_seconds = _optional_float(m.get("wall_seconds"))
            agent_seconds = _optional_float(m.get("agent_process_seconds"))
            reported_seconds = _optional_float(m.get("reported_session_seconds"))
            ended = m.get("finished_at")
            date = datetime.fromtimestamp(ended, timezone.utc).isoformat() if isinstance(ended, (int, float)) else "unknown"
            outcome = r.get("outcome", "unknown")
            has_timeout = "agent_timeout" in r
            row = {
                "run": p.parent.name,
                "model": m.get("observed_model") or m.get("requested_model", "unknown"),
                "task": r.get("task", "unknown"),
                "task_version": _version_field(r, "task_version"),
                "grader_version": _version_field(r, "grader_version"),
                "schema_version": _version_field(r, "schema_version"),
                "image": m.get("image", "unknown"),
                "outcome": outcome,
                "task_score": 100 if outcome == "pass" else 0 if outcome == "fail" else "",
                "agent_termination": r.get("agent_termination", "unknown"),
                "agent_timeout": ("true" if r.get("agent_timeout") else "false") if has_timeout else "unknown",
                "total_workflow_seconds": round(workflow_seconds, 3) if workflow_seconds is not None else "",
                "agent_process_seconds": round(agent_seconds, 3) if agent_seconds is not None else "",
                "reported_session_seconds": round(reported_seconds, 3) if reported_seconds is not None else "",
                "test_date_utc": date,
                "context_size": settings.get("context_size", "unknown"),
                "quantization": settings.get("quantization", "unknown"),
                "gpu_placement": settings.get("gpu_placement", "unknown"),
                "kv_precision": settings.get("kv_precision", "unknown"),
                "sampling": settings.get("sampling", "unknown"),
                "mtp": settings.get("mtp", "unknown"),
                "evidence_directory": str(p.parent.resolve()),
                "infrastructure_errors": json.dumps(r.get("infrastructure_errors", [])),
            }
            for k, v in list(row.items()):
                if isinstance(v, (dict, list)):
                    row[k] = json.dumps(v, sort_keys=True)
            rows.append(row)
        except Exception as exc:
            warnings.append(f"Skipped {p}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("No readable saved results found; no report created.")
    # Group by task identity/version/grader version and the recorded
    # configuration, so different task or grader versions are never
    # merged into one summary row.
    group_fields = [
        "task", "task_version", "grader_version",
        "model", "image", "context_size", "quantization",
        "gpu_placement", "kv_precision", "sampling", "mtp",
    ]
    groups = {}
    for row in rows:
        groups.setdefault(tuple(str(row[k]) for k in group_fields), []).append(row)
    summary = []
    for key, attempts in groups.items():
        valid = [r for r in attempts if r["outcome"] in ("pass", "fail")]
        passes = [r for r in valid if r["outcome"] == "pass"]
        workflow_times = [r["total_workflow_seconds"] for r in passes if r["total_workflow_seconds"] != ""]
        agent_times = [r["agent_process_seconds"] for r in passes if r["agent_process_seconds"] != ""]
        dates = [r["test_date_utc"] for r in attempts if r["test_date_utc"] != "unknown"]
        s = dict(zip(group_fields, key))
        s.update(
            total_attempts=len(attempts),
            valid_attempts=len(valid),
            passes=len(passes),
            failures=len(valid) - len(passes),
            infrastructure_errors=sum(r["outcome"] == "infrastructure_error" for r in attempts),
            other_outcomes=sum(r["outcome"] not in ("pass", "fail", "infrastructure_error") for r in attempts),
            success_percent=round(100 * len(passes) / len(valid), 2) if valid else "",
            median_successful_workflow_seconds=round(statistics.median(workflow_times), 3) if workflow_times else "",
            successful_runs_with_workflow_timing=len(workflow_times),
            median_successful_agent_seconds=round(statistics.median(agent_times), 3) if agent_times else "",
            successful_runs_with_agent_timing=len(agent_times),
            latest_test_utc=max(dates) if dates else "unknown",
        )
        summary.append(s)
    summary.sort(key=lambda s: (
        s["task"], s["task_version"], s["grader_version"],
        -(s["success_percent"] if s["success_percent"] != "" else -1),
        s["median_successful_workflow_seconds"] if s["median_successful_workflow_seconds"] != "" else float("inf"),
        s["model"],
    ))
    folder = base / "reports" / datetime.now(timezone.utc).strftime("report-%Y%m%dT%H%M%S.%fZ")
    folder.mkdir(parents=True, exist_ok=False)

    def write_csv(name, records):
        with (folder / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(records[0]))
            w.writeheader()
            for record in records:
                safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v) for k, v in record.items()}
                w.writerow(safe)

    write_csv("runs.csv", rows)
    write_csv("summary.csv", summary)
    (folder / "REPORT_NOTES.md").write_text("""# Smoke-test results — preliminary

These reports summarize existing result.json files. No inference was run.

- runs.csv: one row per attempt.
- summary.csv: one row per task ID / task version / grader version / recorded configuration. Sort or filter in a spreadsheet.
- task_score is binary: 100 for pass, 0 for fail; infrastructure errors are unscored. It is not general model quality.
- Versions: task_version, grader_version, and schema_version come from the result. Results without these fields are labelled `legacy` (produced by the pre-versioning grader); they are NOT assigned the new version. `legacy` and `2.0.0` rows are never merged.
- Timing is three separate measures, not interchangeable:
  - total_workflow_seconds: whole workflow (container startup + agent + trace export). NOT agent-only latency.
  - agent_process_seconds: measured agent-process duration (monotonic clock), present only in v2 results.
  - reported_session_seconds: duration reported by the session itself, present only in v2 results.
- agent_termination: normal / timeout / sigkill / error / unknown. `timeout` means exit 124 from the wrapper; `sigkill` (exit 137) is recorded separately and is NOT a confirmed timeout.
- agent_timeout is true only for a wrapper-confirmed timeout (exit 124).
- Context, GPU, cache and other unknown settings remain unknown. Do not infer them from model names. Groups with unknown settings may mix different real configurations.
- Summary order is task, task version, grader version, success percentage, then median successful workflow time. This is a preliminary view, NOT a cross-task leaderboard. Image/configuration differences must be considered before comparisons.
- Single trials do not establish reliability. Valid attempts exclude infrastructure errors; their count remains visible.
- CSV files do not retain spreadsheet sorting/formatting; save as XLSX or ODS from your spreadsheet app if desired.
- Existing evidence was read, not modified. New reports were written to a separate timestamped directory.
""", encoding="utf-8")
    print(f"Read {len(rows)} runs; produced {len(summary)} task/version/grader/configuration summaries.")
    for warning in warnings:
        print(warning)
    print(f"REPORT DIRECTORY: {folder}")
    print("Open summary.csv in a spreadsheet to sort by success, time, attempts, or date.")
    print("Timing is split into total workflow, agent-process, and session-reported seconds.")

if __name__ == "__main__":
    print(f"\n{BAR}\nBEGIN OUTPUT TO COPY\n{BAR}", flush=True)
    try:
        main()
    except Exception as exc:
        print(f"REPORT ERROR: {type(exc).__name__}: {exc}")
        sys.exit(3)
    finally:
        print(f"\n{BAR}\nEND OUTPUT TO COPY\n{BAR}", flush=True)

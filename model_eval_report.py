#!/usr/bin/env python3
import csv
import json
import re
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

def _evidence_identifier(run_dir):
    """Derive a project-relative evidence identifier from the
    actual run-directory location.

    The identifier is computed from where the result actually
    lives (runs/<run-id>), never read from or trusting any
    metadata evidence_directory string, so it stays valid when
    the project is moved. Returns "unknown" when a safe
    identifier cannot be established.
    """
    name = run_dir.name
    if name and name not in (".", "..") and "/" not in name and "\\" not in name:
        return f"runs/{name}"
    return "unknown"

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
            # Code provenance: validate the fingerprint scheme
            # and format before using either value. Absent or
            # malformed provenance is "unknown", never invented
            # or inferred from timestamps.
            _scheme = m.get("code_fingerprint_scheme")
            _raw_fp = m.get("code_fingerprint")
            if (
                _scheme == "v1"
                and isinstance(_raw_fp, str)
                and re.fullmatch(r"[0-9a-f]{64}", _raw_fp)
            ):
                code_fingerprint_scheme = "v1"
                code_fingerprint = _raw_fp
                provenance = "recorded fingerprint"
            else:
                code_fingerprint_scheme = "unknown"
                code_fingerprint = "unknown"
                provenance = "unknown provenance"
            _commit = m.get("code_commit")
            code_commit = _commit if isinstance(_commit, str) and _commit else "unknown"
            _dirty = m.get("code_tree_dirty")
            if isinstance(_dirty, bool):
                code_tree_dirty = "true" if _dirty else "false"
            elif isinstance(_dirty, str) and _dirty in ("true", "false"):
                code_tree_dirty = _dirty
            else:
                code_tree_dirty = "unknown"
            # Infrastructure errors are exported as a safe
            # count only. Raw error messages are never written
            # to any report artifact; they stay in result.json.
            # Distinguish an ABSENT field (legacy results have
            # none; backward-compatible zero) from a PRESENT
            # value. A present list counts its entries. A
            # present non-list -- including null -- is invalid
            # report input: the attempt is unscored (outcome
            # invalid_data) so it never counts as a pass, a
            # valid attempt, a successful timed run, or in any
            # success percentage or timing median, while it
            # stays visible in total attempts and the
            # invalid-data count. It is not a model failure
            # and not an infrastructure error. The raw value
            # is never exported (the per-run count shows
            # "unknown").
            _infra_present = "infrastructure_errors" in r
            _infra = r.get("infrastructure_errors")
            infra_invalid = False
            if not _infra_present:
                infra_error_count = 0
            elif isinstance(_infra, list):
                infra_error_count = len(_infra)
            else:
                infra_error_count = "unknown"
                infra_invalid = True
                warnings.append(
                    f"{_evidence_identifier(p.parent)}: "
                    "invalid infrastructure_errors data "
                    "(expected a list); attempt marked "
                    "invalid/unscored"
                )
            if infra_invalid:
                outcome = "invalid_data"
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
                "evidence_directory": _evidence_identifier(p.parent),
                "code_fingerprint_scheme": code_fingerprint_scheme,
                "code_fingerprint": code_fingerprint,
                "provenance": provenance,
                "code_commit": code_commit,
                "code_tree_dirty": code_tree_dirty,
                "infrastructure_errors": infra_error_count,
            }
            for k, v in list(row.items()):
                if isinstance(v, (dict, list)):
                    row[k] = json.dumps(v, sort_keys=True)
            rows.append(row)
        except Exception as exc:
            # Controlled diagnostic only: a project-relative
            # run identifier and a fixed category. No absolute
            # path and no raw exception text is exported.
            if isinstance(exc, json.JSONDecodeError):
                category = "malformed JSON"
            elif isinstance(exc, KeyError):
                category = "invalid result structure"
            else:
                category = "unreadable result"
            warnings.append(
                f"Skipped {_evidence_identifier(p.parent)}: "
                f"{category}"
            )
    if not rows:
        raise RuntimeError("No readable saved results found; no report created.")
    # Group by task identity/version/grader version, the recorded
    # configuration, and the code fingerprint scheme + fingerprint,
    # so different task or grader versions are never merged into
    # one summary row and different evaluator implementations are
    # never compared as one group. A documentation-only commit
    # leaves the fingerprint unchanged, so it does not split a
    # group; a real code change produces a different fingerprint.
    group_fields = [
        "task", "task_version", "grader_version",
        "model", "image", "context_size", "quantization",
        "gpu_placement", "kv_precision", "sampling", "mtp",
        "code_fingerprint_scheme", "code_fingerprint",
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
            invalid_data=sum(r["outcome"] == "invalid_data" for r in attempts),
            other_outcomes=sum(r["outcome"] not in ("pass", "fail", "infrastructure_error", "invalid_data") for r in attempts),
            success_percent=round(100 * len(passes) / len(valid), 2) if valid else "",
            median_successful_workflow_seconds=round(statistics.median(workflow_times), 3) if workflow_times else "",
            successful_runs_with_workflow_timing=len(workflow_times),
            median_successful_agent_seconds=round(statistics.median(agent_times), 3) if agent_times else "",
            successful_runs_with_agent_timing=len(agent_times),
            latest_test_utc=max(dates) if dates else "unknown",
            # Provenance: the scheme and fingerprint are the group
            # identity (every attempt in the group shares them).
            # List ALL distinct commits and dirty states across the
            # group; never select one attempt's values. summary.csv
            # carries no evidence_directory: individual identifiers
            # live in the per-run table (runs.csv).
            provenance="recorded fingerprint" if s["code_fingerprint"] != "unknown" else "unknown provenance",
            code_commits="; ".join(sorted({str(r["code_commit"]) for r in attempts})),
            code_tree_dirty_states="; ".join(sorted({str(r["code_tree_dirty"]) for r in attempts})),
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
- summary.csv: one row per task ID / task version / grader version / recorded configuration / code fingerprint. Sort or filter in a spreadsheet.
- task_score is binary: 100 for pass, 0 for fail; infrastructure errors are unscored. It is not general model quality.
- Versions: task_version, grader_version, and schema_version come from the result. Results without these fields are labelled `legacy` (produced by the pre-versioning grader); they are NOT assigned the new version. `legacy` and `2.0.0` rows are never merged.
- evidence_directory (runs.csv only): a project-relative identifier, `runs/<run-id>`, derived from where each result actually lives. It is never read from metadata, so it stays valid when the project is moved. summary.csv does not carry it; see runs.csv (the per-run table) for individual evidence identifiers.
- Code provenance: `code_fingerprint_scheme` + `code_fingerprint` (scheme v1 = sha256 over the sorted filename + sha256(content) of run_v2.sh, worker_v2.sh, grade_v2.py, tasklib.py, and the exact task snapshot) identify the evaluation code and task. `code_commit` and `code_tree_dirty` are recorded separately. Runs group by fingerprint scheme + fingerprint, so documentation-only commits do not split equivalent implementations. Runs without a recorded fingerprint show `unknown provenance` and stay distinct from runs with a recorded fingerprint. Provenance is never invented or inferred from timestamps.
- summary.csv `code_commits` and `code_tree_dirty_states` list every distinct commit and dirty state across the group, not one attempt's values.
- `infrastructure_errors` is a safe count in both `runs.csv` (entries for that attempt) and `summary.csv` (attempts with an infrastructure-error outcome). A legacy result with no `infrastructure_errors` field counts as zero. A present non-list value (including null) is invalid report input: the attempt is exported with outcome `invalid_data` and counted in `summary.csv`'s `invalid_data` column — unscored (not a pass, valid attempt, successful timed run, or in any success percentage or timing median), but visible in total attempts and the invalid-data count. It is not a model failure and not an infrastructure error. The raw malformed value is never exported (the per-run count shows `unknown`). Raw infrastructure-error messages are never exported to runs.csv, summary.csv, REPORT_NOTES.md, or the dashboard HTML; they remain only in the local result.json.
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

"""No-inference tests for the reporting layer's privacy and
run provenance (v2).

Builds synthetic result.json fixtures in temp directories
OUTSIDE the real runs/, then runs the reporter and the
dashboard as subprocesses and inspects every generated
output (runs.csv, summary.csv, REPORT_NOTES.md, and the
dashboard HTML). No model inference, no real Podman/Hermes.

Limits: these tests verify the known export fields and the
location-derived identifier on fixtures. They are NOT proof
that arbitrary free text can never contain sensitive
information. The reporter does not export failure_reasons,
base_url, api_key, or raw infrastructure-error messages;
infrastructure_errors is exported as a safe count only. If
a future change exports free-text fields, those would need
their own sanitization and tests.
"""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

REPORTER = PROJECT / "model_eval_report.py"
DASHBOARD = PROJECT / "model_eval_dashboard.py"

# Known-good scheme-v1 values (64-hex fingerprint, 40-hex commit).
KNOWN_FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
KNOWN_COMMIT = "c0ffee" + "0" * 34

DEFAULT_SETTINGS = {
    "context_size": "unknown",
    "quantization": "unknown",
    "gpu_placement": "unknown",
    "kv_precision": "unknown",
    "sampling": "unknown",
    "mtp": "unknown",
}


_UNSET = object()  # distinguishes "not passed" from an explicit None


def make_result(*, provenance=None, evidence_directory=None,
                settings=None, outcome="pass",
                infrastructure_errors=_UNSET):
    """Build a synthetic result.json dict.

    provenance: optional dict of code-provenance metadata fields.
    evidence_directory: a distrusted metadata string the reporter
    must ignore (the identifier is derived from the location).
    """
    metadata = {
        "requested_model": "test-model",
        "observed_model": "test-model",
        "image": "test-image@sha256:deadbeef",
        "settings": settings or dict(DEFAULT_SETTINGS),
        "wall_seconds": 26.614,
        "agent_process_seconds": 22.462,
        "reported_session_seconds": 7.718,
        "finished_at": 1_800_000_000.0,
        # Sensitive values that must never reach any export.
        "base_url": "http://192.0.2.123:1234/v1",
        "api_key": "sk-super-secret-token",
    }
    if provenance:
        metadata.update(provenance)
    if evidence_directory is not None:
        metadata["evidence_directory"] = evidence_directory
    return {
        "schema_version": 2,
        "task": "exact-file-v2",
        "task_version": 1,
        "task_schema_version": 1,
        "grader_version": "2.0.0",
        "outcome": outcome,
        "checks": {
            "agent_exit_zero": True, "exact_bytes": True,
            "write_tool_completed": True,
            "read_after_write_completed": True,
            "requested_model": True, "expected_endpoint": True,
            "allowed_tools_respected": True,
        },
        "failure_reasons": [],
        "agent_timeout": False,
        "agent_termination": "normal",
        "agent_exit": 0,
        "prohibited_tools": [],
        "infrastructure_errors": ([] if infrastructure_errors is _UNSET
                                  else infrastructure_errors),
        "metadata": metadata,
    }


def write_run(base, run_id, result):
    run = base / "runs" / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return run


class ReportPrivacyV2Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="report-privacy-v2-")
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # --- helpers -------------------------------------------------------
    def run_reporter(self, base=None):
        base = base or self.base
        proc = subprocess.run(
            [sys.executable, str(REPORTER), str(base)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0,
                         f"reporter stderr:\n{proc.stderr}")
        reports = sorted((base / "reports").glob("report-*"))
        self.assertEqual(len(reports), 1)
        return reports[0]

    def run_reporter_full(self, base=None):
        """Run the reporter, returning (report_dir, proc) so
        tests can inspect its stdout/stderr too."""
        base = base or self.base
        proc = subprocess.run(
            [sys.executable, str(REPORTER), str(base)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0,
                         f"reporter stderr:\n{proc.stderr}")
        reports = sorted((base / "reports").glob("report-*"))
        self.assertEqual(len(reports), 1)
        return reports[0], proc

    def run_dashboard(self, report_dir):
        proc = subprocess.run(
            [sys.executable, str(DASHBOARD), str(report_dir)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0,
                         f"dashboard stderr:\n{proc.stderr}")
        htmls = sorted(report_dir.glob("dashboard-*.html"))
        self.assertEqual(len(htmls), 1)
        return htmls[0]

    def read_csv(self, report_dir, name):
        with (report_dir / name).open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def all_output_text(self, report_dir, html_path):
        parts = []
        for name in ("runs.csv", "summary.csv", "REPORT_NOTES.md"):
            parts.append((report_dir / name).read_text(encoding="utf-8"))
        parts.append(html_path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    # --- A. portable evidence references -------------------------------
    def test_evidence_identifier_is_relative_not_absolute(self):
        """The identifier is runs/<run-id>, never an absolute path,
        even when metadata carries an absolute evidence_directory."""
        write_run(self.base, "run.5W8oobTG", make_result(
            evidence_directory="/home/someuser/hermes-eval/runs/run.5W8oobTG"))
        report = self.run_reporter()
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence_directory"], "runs/run.5W8oobTG")
        html = self.run_dashboard(report)
        text = self.all_output_text(report, html)
        self.assertNotIn("/home/", text)
        self.assertNotIn(str(self.base), text)

    def test_evidence_identifier_is_move_safe(self):
        """The identifier depends only on the run-id, not the
        project's absolute location."""
        write_run(self.base, "run.TQQj4VI3", make_result())
        report_a = self.run_reporter()
        id_a = self.read_csv(report_a, "runs.csv")[0]["evidence_directory"]
        # Rebuild the identical fixture under a different base path.
        other = self.base / "moved" / "elsewhere"
        other.mkdir(parents=True)
        write_run(other, "run.TQQj4VI3", make_result())
        report_b = self.run_reporter(base=other)
        id_b = self.read_csv(report_b, "runs.csv")[0]["evidence_directory"]
        self.assertEqual(id_a, "runs/run.TQQj4VI3")
        self.assertEqual(id_b, "runs/run.TQQj4VI3")
        self.assertEqual(id_a, id_b)

    def test_malformed_path_traversal_metadata_ignored(self):
        """A traversal or absolute metadata evidence_directory is
        never trusted; the identifier is derived from the location."""
        for bad in ("../../etc/passwd", "/etc/passwd",
                    "runs/../../../etc/shadow", "..", "."):
            with self.subTest(bad=bad):
                base = self.base / f"trav-{uuid.uuid4().hex[:6]}"
                base.mkdir()
                write_run(base, "run.JHZ5yJ2U",
                          make_result(evidence_directory=bad))
                report = self.run_reporter(base=base)
                rows = self.read_csv(report, "runs.csv")
                self.assertEqual(
                    rows[0]["evidence_directory"], "runs/run.JHZ5yJ2U")
                html = self.run_dashboard(report)
                text = self.all_output_text(report, html)
                self.assertNotIn("../", text)
                self.assertNotIn("/etc/", text)

    # --- D. sensitive fields are not exported ------------------------
    def test_sensitive_fields_not_exported(self):
        """Credentials and endpoints recorded in metadata must not
        appear in any generated output."""
        write_run(self.base, "run.X", make_result())
        report = self.run_reporter()
        html = self.run_dashboard(report)
        text = self.all_output_text(report, html)
        self.assertNotIn("sk-super-secret-token", text)
        self.assertNotIn("192.0.2.123", text)
        self.assertNotIn("http://192.0.2.123", text)
        self.assertNotIn("base_url", text)
        self.assertNotIn("api_key", text)

    def test_infrastructure_error_messages_not_exported(self):
        """Raw infrastructure-error messages (fake endpoint,
        absolute-path, and credential-shaped strings) must not
        reach any export artifact; only a safe count is exported."""
        infra = [
            "connect failed http://10.0.0.99:1234/v1",
            "proof missing under /home/victim/secret.txt",
            "api_key sk-EXAMPLE-NOT-A-REAL-KEY rejected",
        ]
        write_run(self.base, "run.INFRA", make_result(
            outcome="infrastructure_error",
            infrastructure_errors=infra))
        report = self.run_reporter()
        html = self.run_dashboard(report)
        text = self.all_output_text(report, html)
        for secret in infra:
            self.assertNotIn(secret, text)
        # Distinctive fragments must not leak either.
        for fragment in ("10.0.0.99", "/home/victim",
                          "sk-EXAMPLE-NOT-A-REAL-KEY"):
            self.assertNotIn(fragment, text)
        # The safe count remains correct.
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(rows[0]["infrastructure_errors"], "3")
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(summary[0]["infrastructure_errors"], "1")

    def test_malformed_result_warning_has_no_path_or_exception_text(self):
        """A malformed result is skipped with a project-relative
        run identifier and a controlled category -- no absolute
        path and no raw exception text -- while a valid fixture
        still produces a report."""
        # Valid fixture so report generation proceeds.
        write_run(self.base, "run.GOOD", make_result())
        # Malformed fixture whose content carries sensitive strings.
        bad = self.base / "runs" / "run.malformed"
        bad.mkdir(parents=True)
        (bad / "result.json").write_text(
            '{"metadata": {"base_url": "http://10.0.0.99:1234/v1", '
            '"api_key": "sk-EXAMPLE-NOT-A-REAL-KEY", '
            '"path": "/home/victim/secret.txt"} UNTERMINATED',
            encoding="utf-8",
        )
        report, proc = self.run_reporter_full()
        html = self.run_dashboard(report)
        # The valid run is reported; the malformed one is skipped.
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run"], "run.GOOD")
        # Combined text of every export artifact plus the
        # reporter's own stdout and stderr.
        text = (self.all_output_text(report, html) + "\n"
                + proc.stdout + "\n" + proc.stderr)
        for secret in ("http://10.0.0.99:1234/v1",
                        "/home/victim/secret.txt",
                        "sk-EXAMPLE-NOT-A-REAL-KEY"):
            self.assertNotIn(secret, text)
        # The skip warning uses the relative identifier, not
        # the absolute result path.
        self.assertNotIn(str(bad), proc.stdout)
        self.assertNotIn(str(bad / "result.json"), proc.stdout)
        # No raw exception type name -- a controlled category
        # is used instead.
        self.assertNotIn("JSONDecodeError", proc.stdout)
        self.assertNotIn("Traceback", proc.stdout)
        # The warning path was exercised and names the run.
        self.assertIn("runs/run.malformed", proc.stdout)

    def test_malformed_infrastructure_errors_unscored(self):
        """A present non-list infrastructure_errors (null, string,
        object, or numeric) is invalid report input: the apparent
        pass is exported as outcome 'invalid_data' and excluded
        from every successful/valid summary metric, while
        remaining visible in total attempts and the invalid-data
        count. The raw malformed value is never exported."""
        malformed_values = [
            None,           # present null
            "not-a-list",   # string
            {"count": 2},   # object
            7,              # numeric
        ]
        for i, bad in enumerate(malformed_values):
            with self.subTest(malformed=repr(bad)):
                base = tempfile.TemporaryDirectory()
                self.addCleanup(base.cleanup)
                self.base = Path(base.name)
                # A valid pass alongside the malformed record.
                write_run(self.base, "run.GOOD", make_result(
                    outcome="pass", infrastructure_errors=[]))
                # An apparent pass carrying a malformed field
                # and distinctive timing, so exclusion from the
                # timing medians is observable.
                bad_result = make_result(
                    outcome="pass", infrastructure_errors=bad)
                # The reporter derives timing from wall_seconds
                # and agent_process_seconds; give the malformed
                # record distinctive values so exclusion from the
                # medians is observable.
                bad_result["metadata"]["wall_seconds"] = 999.0
                bad_result["metadata"]["agent_process_seconds"] = 999.0
                write_run(self.base, f"run.BAD{i}", bad_result)
                report = self.run_reporter()
                # The malformed record is unscored; the valid
                # pass is unaffected.
                rows = self.read_csv(report, "runs.csv")
                bad_row = next(
                    r for r in rows if r["run"] == f"run.BAD{i}")
                self.assertEqual(bad_row["outcome"], "invalid_data")
                self.assertEqual(bad_row["infrastructure_errors"],
                                 "unknown")
                self.assertEqual(bad_row["task_score"], "")
                good_row = next(
                    r for r in rows if r["run"] == "run.GOOD")
                self.assertEqual(good_row["outcome"], "pass")
                self.assertEqual(good_row["infrastructure_errors"], "0")
                # The raw malformed value is never exported.
                runs_csv = (report / "runs.csv").read_text(
                    encoding="utf-8-sig")
                self.assertNotIn("not-a-list", runs_csv)
                self.assertNotIn("count", runs_csv)
                # Summary: the malformed record is excluded from
                # all successful/valid metrics but visible as
                # invalid.
                summary = self.read_csv(report, "summary.csv")[0]
                self.assertEqual(summary["total_attempts"], "2")
                self.assertEqual(summary["valid_attempts"], "1")
                self.assertEqual(summary["passes"], "1")
                self.assertEqual(summary["failures"], "0")
                self.assertEqual(summary["infrastructure_errors"], "0")
                self.assertEqual(summary["invalid_data"], "1")
                self.assertEqual(summary["other_outcomes"], "0")
                self.assertEqual(summary["success_percent"], "100.0")
                # Excluded from successful timing: the medians
                # and timed-run counts come from the one valid
                # pass only, not the malformed record's
                # 999-second timing.
                self.assertEqual(
                    summary["successful_runs_with_workflow_timing"], "1")
                self.assertEqual(
                    summary["median_successful_workflow_seconds"], "26.614")
                self.assertEqual(
                    summary["successful_runs_with_agent_timing"], "1")
                self.assertEqual(
                    summary["median_successful_agent_seconds"], "22.462")

    def test_missing_infrastructure_errors_defaults_to_zero(self):
        """A legacy result with no infrastructure_errors field
        is backward-compatible: the count defaults to zero and
        the attempt stays a valid, scored pass (not invalid)."""
        result = make_result(outcome="pass")
        del result["infrastructure_errors"]  # legacy result
        write_run(self.base, "run.LEGACY", result)
        report = self.run_reporter()
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(rows[0]["infrastructure_errors"], "0")
        self.assertEqual(rows[0]["outcome"], "pass")
        self.assertEqual(rows[0]["task_score"], "100")
        summary = self.read_csv(report, "summary.csv")[0]
        self.assertEqual(summary["valid_attempts"], "1")
        self.assertEqual(summary["passes"], "1")
        self.assertEqual(summary["invalid_data"], "0")
        self.assertEqual(summary["success_percent"], "100.0")

    # --- B/C. provenance known / unknown / malformed -----------------
    def test_provenance_recorded(self):
        write_run(self.base, "run.A", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": KNOWN_COMMIT,
            "code_tree_dirty": "false",
        }))
        report = self.run_reporter()
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(rows[0]["provenance"], "recorded fingerprint")
        self.assertEqual(rows[0]["code_fingerprint_scheme"], "v1")
        self.assertEqual(rows[0]["code_fingerprint"], KNOWN_FINGERPRINT)
        self.assertEqual(rows[0]["code_commit"], KNOWN_COMMIT)
        self.assertEqual(rows[0]["code_tree_dirty"], "false")
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(summary[0]["provenance"], "recorded fingerprint")

    def test_provenance_unknown_when_absent(self):
        write_run(self.base, "run.B", make_result())  # no provenance fields
        report = self.run_reporter()
        rows = self.read_csv(report, "runs.csv")
        self.assertEqual(rows[0]["provenance"], "unknown provenance")
        self.assertEqual(rows[0]["code_fingerprint_scheme"], "unknown")
        self.assertEqual(rows[0]["code_fingerprint"], "unknown")
        self.assertEqual(rows[0]["code_commit"], "unknown")
        self.assertEqual(rows[0]["code_tree_dirty"], "unknown")

    def test_provenance_malformed_rejected(self):
        """A wrong scheme or a non-hex/short fingerprint is treated
        as unknown provenance, not trusted."""
        cases = [
            {"code_fingerprint_scheme": "v9",
             "code_fingerprint": KNOWN_FINGERPRINT},
            {"code_fingerprint_scheme": "v1", "code_fingerprint": "not-hex"},
            {"code_fingerprint_scheme": "v1", "code_fingerprint": "abc123"},
            {"code_fingerprint_scheme": "v1",
             "code_fingerprint": KNOWN_FINGERPRINT.upper()},
        ]
        for prov in cases:
            with self.subTest(prov=prov):
                base = self.base / f"mal-{uuid.uuid4().hex[:6]}"
                base.mkdir()
                write_run(base, "run.C", make_result(provenance=prov))
                report = self.run_reporter(base=base)
                rows = self.read_csv(report, "runs.csv")
                self.assertEqual(rows[0]["provenance"], "unknown provenance")
                self.assertEqual(rows[0]["code_fingerprint"], "unknown")

    # --- C. grouping --------------------------------------------------
    def test_grouping_same_fingerprint_one_group(self):
        """Two runs with the same fingerprint but different commits
        (a documentation-only commit scenario) form ONE group."""
        write_run(self.base, "run.D1", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": "commit-one" + "0" * 30,
            "code_tree_dirty": "false"}))
        write_run(self.base, "run.D2", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": "commit-two" + "0" * 30,
            "code_tree_dirty": "false"}))
        report = self.run_reporter()
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(len(summary), 1)  # one group
        # All distinct commits are listed, not one attempt's value.
        self.assertIn("commit-one" + "0" * 30, summary[0]["code_commits"])
        self.assertIn("commit-two" + "0" * 30, summary[0]["code_commits"])
        self.assertEqual(summary[0]["code_commits"].count(";"), 1)

    def test_grouping_different_fingerprint_two_groups(self):
        write_run(self.base, "run.E1", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": KNOWN_COMMIT, "code_tree_dirty": "false"}))
        write_run(self.base, "run.E2", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": OTHER_FINGERPRINT,
            "code_commit": KNOWN_COMMIT, "code_tree_dirty": "false"}))
        report = self.run_reporter()
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(len(summary), 2)  # different implementations

    def test_grouping_legacy_vs_recorded_distinct(self):
        """A legacy run (no fingerprint) and a recorded run with the
        same task/grader/model do NOT silently merge."""
        write_run(self.base, "run.F1", make_result())  # legacy
        write_run(self.base, "run.F2", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": KNOWN_COMMIT, "code_tree_dirty": "false"}))
        report = self.run_reporter()
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(len(summary), 2)
        provenances = {row["provenance"] for row in summary}
        self.assertEqual(provenances,
                         {"unknown provenance", "recorded fingerprint"})

    def test_summary_aggregates_all_commits_and_dirty_states(self):
        write_run(self.base, "run.G1", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": "aaa" + "0" * 37, "code_tree_dirty": "true"}))
        write_run(self.base, "run.G2", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": "bbb" + "0" * 37, "code_tree_dirty": "false"}))
        report = self.run_reporter()
        summary = self.read_csv(report, "summary.csv")
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["code_commits"],
                         "aaa" + "0" * 37 + "; " + "bbb" + "0" * 37)
        self.assertEqual(summary[0]["code_tree_dirty_states"], "false; true")

    def test_summary_has_no_evidence_directory_column(self):
        """Individual evidence identifiers live in the per-run table,
        not the aggregate summary."""
        write_run(self.base, "run.H", make_result())
        report = self.run_reporter()
        with (report / "summary.csv").open(
                encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            self.assertNotIn("evidence_directory", reader.fieldnames)
        notes = (report / "REPORT_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("per-run table", notes)

    # --- dashboard column presence ------------------------------------
    def test_dashboard_columns_present(self):
        write_run(self.base, "run.I", make_result(provenance={
            "code_fingerprint_scheme": "v1",
            "code_fingerprint": KNOWN_FINGERPRINT,
            "code_commit": KNOWN_COMMIT, "code_tree_dirty": "false"}))
        report = self.run_reporter()
        html = self.run_dashboard(report)
        text = html.read_text(encoding="utf-8")
        for label in ("Fingerprint scheme", "Code fingerprint",
                      "Provenance", "Code commit", "Tree dirty",
                      "Evidence (project-relative)",
                      "Code commits", "Tree dirty states",
                      "Infrastructure error entries",
                      "Attempts with infrastructure errors",
                      "Invalid data attempts"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()

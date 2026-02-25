from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.config import load_config  # noqa: E402
from project_os_agent_lib.kpi import (  # noqa: E402
    _compute_docs_freshness,
    _compute_kpis,
    _compute_pipeline_green_streak,
    run_report_kpis,
)


class ReportKpiCommandTests(unittest.TestCase):
    def test_run_report_kpis_stdout_only_returns_markdown_and_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                since_days=None,
                output=None,
                stdout_only=True,
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = run_report_kpis(args)

            output = buffer.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("# Project OS Agent Weekly KPI Report", output)
            self.assertIn("docs_freshness_ratio", output)

    def test_run_report_kpis_raises_error_for_invalid_since_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                since_days=-1,
                output=None,
                stdout_only=True,
            )

            with self.assertRaises(ValueError) as ctx:
                run_report_kpis(args)

            self.assertIn("since-days must be >= 1", str(ctx.exception))


class KpiHelpersTests(unittest.TestCase):
    def test_compute_pipeline_green_streak_counts_trailing_successes(self) -> None:
        self.assertEqual(_compute_pipeline_green_streak([]), 0)
        self.assertEqual(_compute_pipeline_green_streak(["failed", "success"]), 1)
        self.assertEqual(_compute_pipeline_green_streak(["success", "success", "passed"]), 3)
        self.assertEqual(_compute_pipeline_green_streak(["success", "failed", "success"]), 1)

    def test_compute_docs_freshness_handles_fresh_stale_and_missing_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            now_utc = datetime(2026, 2, 1, tzinfo=timezone.utc)
            stale_threshold = now_utc - timedelta(days=config.phase3.stale_days + 1)

            managed_targets = [mapping.target for mapping in config.managed_files]
            self.assertGreaterEqual(len(managed_targets), 3)
            fresh_target = temp_root / managed_targets[0]
            stale_target = temp_root / managed_targets[1]

            fresh_target.write_text("# fresh", encoding="utf-8")
            stale_target.write_text("# stale", encoding="utf-8")
            os.utime(stale_target, (stale_threshold.timestamp(), stale_threshold.timestamp()))

            metrics = _compute_docs_freshness(temp_root, config, now_utc)
            self.assertEqual(metrics["total"], len(managed_targets))
            self.assertEqual(metrics["fresh"], 1)
            self.assertEqual(metrics["stale"], 1)
            self.assertEqual(metrics["missing"], len(managed_targets) - 2)

    def test_compute_kpis_aggregates_command_and_operation_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            now_utc = datetime(2026, 2, 10, tzinfo=timezone.utc)

            events = [
                {
                    "category": "command",
                    "command": "process-event",
                    "duration_ms": 1200,
                    "event_type": "pipeline",
                    "details": {"event_state": "failed", "blocked_actions_count": 1},
                },
                {
                    "category": "command",
                    "command": "sync-docs",
                    "duration_ms": 800,
                    "details": {"updated_count": 2, "created_count": 1},
                },
                {
                    "category": "command",
                    "command": "process-event",
                    "duration_ms": 1000,
                    "event_type": "pipeline",
                    "details": {"event_state": "success"},
                },
                {
                    "category": "operation",
                    "status": "applied",
                    "details": {"op_type": "open_merge_request"},
                },
            ]

            metrics = _compute_kpis(temp_root, config, events, now_utc)
            self.assertEqual(metrics["events_count"], 4)
            self.assertEqual(metrics["commands_count"], 3)
            self.assertEqual(metrics["operations_count"], 1)
            self.assertEqual(metrics["blocked_actions_count"], 1)
            self.assertEqual(metrics["docs_update_actions"], 3)
            self.assertEqual(metrics["pipeline_failure_events"], 1)
            self.assertEqual(metrics["pipeline_green_streak"], 1)
            self.assertEqual(metrics["mr_open_operations_total"], 1)
            self.assertEqual(metrics["mr_open_success_ratio"], 1.0)
            self.assertEqual(metrics["mean_response_time_seconds"], 1.0)


class ReportKpiWriteTests(unittest.TestCase):
    def test_run_report_kpis_writes_report_file_when_stdout_only_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            audit_dir = temp_root / ".project-os-agent"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit_log = audit_dir / "audit.log.jsonl"
            audit_log.write_text(json.dumps({"category": "command"}) + "\n", encoding="utf-8")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                since_days=7,
                output=".project-os-agent/reports/custom-kpi.md",
                stdout_only=False,
            )
            with redirect_stdout(io.StringIO()):
                exit_code = run_report_kpis(args)
            self.assertEqual(exit_code, 0)
            report_path = temp_root / ".project-os-agent/reports/custom-kpi.md"
            self.assertTrue(report_path.exists())
            self.assertIn(
                "Project OS Agent Weekly KPI Report",
                report_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()


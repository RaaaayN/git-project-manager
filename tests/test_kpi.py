from __future__ import annotations

import argparse
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.kpi import run_report_kpis  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()


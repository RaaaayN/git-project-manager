from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.diagnose import run_diagnose  # noqa: E402


class DiagnoseCommandTests(unittest.TestCase):
    def test_run_diagnose_stdout_only_prints_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                output=None,
                stdout_only=True,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                exit_code = run_diagnose(args)
            self.assertEqual(exit_code, 0)
            content = out.getvalue()
            self.assertIn("# Project OS Agent CLI Diagnostics", content)
            self.assertIn("Managed Documentation", content)

    def test_run_diagnose_writes_file_when_stdout_only_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                output=".project-os-agent/reports/diag.md",
                stdout_only=False,
            )
            with redirect_stdout(io.StringIO()):
                exit_code = run_diagnose(args)
            self.assertEqual(exit_code, 0)
            report_path = temp_root / ".project-os-agent/reports/diag.md"
            self.assertTrue(report_path.exists())

    def test_run_diagnose_rejects_output_path_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            outside_file = (Path(temp_dir).parent / "outside-diagnose.md").resolve()
            relative_escape = os.path.relpath(outside_file, start=temp_root)

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                output=relative_escape,
                stdout_only=False,
            )
            with self.assertRaises(ValueError):
                run_diagnose(args)


if __name__ == "__main__":
    unittest.main()


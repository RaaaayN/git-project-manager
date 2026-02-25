from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.guardrails import GuardrailViolation  # noqa: E402
from project_os_agent_lib.pipeline import run_process_event  # noqa: E402
from project_os_agent_lib.workflows import run_dry_run_global  # noqa: E402


def _issue_payload() -> dict[str, object]:
    return {
        "object_kind": "issue",
        "object_attributes": {
            "action": "open",
            "state": "opened",
            "title": "Investigate pipeline reliability",
            "iid": 22,
            "description": "This issue tracks pipeline reliability improvements. Acceptance criteria: - [ ] KPI report generated",
        },
        "project": {"path_with_namespace": "group/project"},
        "user": {"username": "alice"},
    }


class DryRunGlobalWorkflowTests(unittest.TestCase):
    def test_run_dry_run_global_outputs_json_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                event_name="Issue Hook",
                payload_file=None,
                payload_json=json.dumps(_issue_payload()),
                since_days=7,
                no_kpi_markdown=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = run_dry_run_global(args)

            self.assertEqual(exit_code, 0)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["mode"], "dry-run-global")
            self.assertIn("bootstrap", summary)
            self.assertIn("event", summary)
            self.assertIn("kpis", summary)

    def test_run_dry_run_global_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            raw_config = (REPO_ROOT / ".project-os-agent.yml").read_text(encoding="utf-8")
            config_path.write_text(raw_config.replace("524288", "1024"), encoding="utf-8")

            oversized_payload = {"blob": "a" * 5000}
            args = argparse.Namespace(
                config=str(config_path),
                event_name="Issue Hook",
                payload_file=None,
                payload_json=json.dumps(oversized_payload),
                since_days=7,
                no_kpi_markdown=True,
            )
            with self.assertRaises(GuardrailViolation):
                run_dry_run_global(args)

    def test_run_dry_run_global_enforces_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            raw_config = (REPO_ROOT / ".project-os-agent.yml").read_text(encoding="utf-8")
            config_path.write_text(
                raw_config.replace("max_events: 30", "max_events: 1"),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                event_name="Issue Hook",
                payload_file=None,
                payload_json=json.dumps(_issue_payload()),
                since_days=7,
                no_kpi_markdown=True,
            )
            with redirect_stdout(io.StringIO()):
                first_exit = run_dry_run_global(args)
            self.assertEqual(first_exit, 0)
            with self.assertRaises(GuardrailViolation):
                run_dry_run_global(args)


class CliE2EScenariosTests(unittest.TestCase):
    def test_run_process_event_end_to_end_returns_plan_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            args = argparse.Namespace(
                config=str(temp_root / ".project-os-agent.yml"),
                event_name="Issue Hook",
                payload_file=None,
                payload_json=json.dumps(_issue_payload()),
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = run_process_event(args)
            self.assertEqual(exit_code, 0)
            plan_json = json.loads(output.getvalue())
            self.assertEqual(plan_json["normalized_event"]["event_type"], "issue")
            self.assertIn("proposed_actions", plan_json)


if __name__ == "__main__":
    unittest.main()


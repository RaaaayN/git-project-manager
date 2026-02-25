from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent import parse_args  # noqa: E402
from project_os_agent_lib.cli_validation import (  # noqa: E402
    resolve_since_days,
    resolve_webhook_settings,
)
from project_os_agent_lib.config import WebhookConfig  # noqa: E402


class ParseArgsTests(unittest.TestCase):
    def test_known_commands_are_parsed(self) -> None:
        for command in [
            "bootstrap",
            "process-event",
            "sync-docs",
            "act",
            "serve-webhook",
            "report-kpis",
            "diagnose",
            "dry-run-global",
        ]:
            args = parse_args([command])
            self.assertEqual(args.command, command)

    def test_unknown_first_argument_falls_back_to_bootstrap(self) -> None:
        args = parse_args(["--apply"])
        self.assertEqual(args.command, "bootstrap")
        self.assertTrue(args.apply)

    def test_report_kpis_and_diagnose_flags(self) -> None:
        report_args = parse_args(["report-kpis", "--stdout-only", "--since-days", "3"])
        self.assertTrue(report_args.stdout_only)
        self.assertEqual(report_args.since_days, 3)

        diagnose_args = parse_args(["diagnose", "--stdout-only", "--output", "tmp/report.md"])
        self.assertTrue(diagnose_args.stdout_only)
        self.assertEqual(diagnose_args.output, "tmp/report.md")


class CliValidationTests(unittest.TestCase):
    def test_resolve_since_days_applies_default_and_rejects_invalid_values(self) -> None:
        self.assertEqual(resolve_since_days(None, 7), 7)
        self.assertEqual(resolve_since_days(1, 7), 1)
        with self.assertRaises(ValueError):
            resolve_since_days(0, 7)
        with self.assertRaises(ValueError):
            resolve_since_days(-5, 7)

    def test_resolve_webhook_settings_validates_and_normalizes(self) -> None:
        config = WebhookConfig(
            enabled=True,
            host="0.0.0.0",
            port=8080,
            path="/webhooks/gitlab",
            secret_env="GITLAB_WEBHOOK_SECRET",
        )
        host, port, path = resolve_webhook_settings(
            config,
            host_override="127.0.0.1",
            port_override=9000,
            path_override="webhooks/custom",
        )
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 9000)
        self.assertEqual(path, "/webhooks/custom")

        with self.assertRaises(ValueError):
            resolve_webhook_settings(
                config,
                host_override="",
                port_override=9000,
                path_override="/ok",
            )
        with self.assertRaises(ValueError):
            resolve_webhook_settings(
                config,
                host_override="127.0.0.1",
                port_override=70000,
                path_override="/ok",
            )


if __name__ == "__main__":
    unittest.main()


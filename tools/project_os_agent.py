#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from project_os_agent_lib.actor import run_act
from project_os_agent_lib.diagnose import run_diagnose
from project_os_agent_lib.kpi import run_report_kpis
from project_os_agent_lib.pipeline import (
    run_bootstrap,
    run_process_event,
    run_serve_webhook,
    run_sync_docs,
)
from project_os_agent_lib.workflows import run_dry_run_global


def parse_args(argv: list[str]) -> argparse.Namespace:
    known_commands = {
        "bootstrap",
        "process-event",
        "sync-docs",
        "act",
        "serve-webhook",
        "report-kpis",
        "diagnose",
        "dry-run-global",
    }
    normalized_argv = list(argv)
    if not normalized_argv:
        normalized_argv = ["bootstrap"]
    elif normalized_argv[0] not in known_commands and normalized_argv[0] not in {"-h", "--help"}:
        normalized_argv = ["bootstrap", *normalized_argv]

    parser = argparse.ArgumentParser(description="Project OS Agent CLI")
    subparsers = parser.add_subparsers(dest="command")

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="Phase 1 bootstrap: create missing docs from templates.",
    )
    bootstrap.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config (default: .project-os-agent.yml).",
    )
    mode_group = bootstrap.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without writing files.",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write missing files to disk.",
    )
    bootstrap.add_argument(
        "--no-diff",
        action="store_true",
        help="Hide unified diffs in output.",
    )

    process_event = subparsers.add_parser(
        "process-event",
        help="Phase 2: normalize a GitLab event and produce an action plan.",
    )
    process_event.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    process_event.add_argument(
        "--event-name",
        default="",
        help='GitLab event name from header (for example "Merge Request Hook").',
    )
    payload_group = process_event.add_mutually_exclusive_group()
    payload_group.add_argument(
        "--payload-file",
        help="Read JSON payload from file.",
    )
    payload_group.add_argument(
        "--payload-json",
        help="Read JSON payload from a command-line string.",
    )

    sync_docs = subparsers.add_parser(
        "sync-docs",
        help="Phase 3: apply idempotent markdown updates using event plan.",
    )
    sync_docs.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    sync_docs.add_argument(
        "--event-name",
        default="",
        help='GitLab event name from header (for example "Merge Request Hook").',
    )
    sync_payload_group = sync_docs.add_mutually_exclusive_group()
    sync_payload_group.add_argument(
        "--payload-file",
        help="Read JSON payload from file.",
    )
    sync_payload_group.add_argument(
        "--payload-json",
        help="Read JSON payload from a command-line string.",
    )
    sync_mode_group = sync_docs.add_mutually_exclusive_group()
    sync_mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview markdown/doc updates without writing files.",
    )
    sync_mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write markdown/doc updates to disk.",
    )
    sync_docs.add_argument(
        "--no-diff",
        action="store_true",
        help="Hide unified diffs for markdown/doc updates.",
    )

    act = subparsers.add_parser(
        "act",
        help="Phase 4: execute GitLab actor operations (branches, MR, issues, comments).",
    )
    act.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    act.add_argument(
        "--event-name",
        default="",
        help='GitLab event name from header (for example "Merge Request Hook").',
    )
    act_payload_group = act.add_mutually_exclusive_group()
    act_payload_group.add_argument(
        "--payload-file",
        help="Read JSON payload from file.",
    )
    act_payload_group.add_argument(
        "--payload-json",
        help="Read JSON payload from a command-line string.",
    )
    act_mode_group = act.add_mutually_exclusive_group()
    act_mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview GitLab actor operations without API writes.",
    )
    act_mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Execute GitLab actor operations.",
    )
    act.add_argument(
        "--skip-sync-docs",
        action="store_true",
        help="Skip Phase 3 sync before Phase 4 actor execution.",
    )
    act.add_argument(
        "--no-diff",
        action="store_true",
        help="Hide markdown diff output during auto sync.",
    )

    serve_webhook = subparsers.add_parser(
        "serve-webhook",
        help="Phase 2: run local webhook endpoint for GitLab events.",
    )
    serve_webhook.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    serve_webhook.add_argument("--host", help="Override webhook host from config.")
    serve_webhook.add_argument(
        "--port",
        type=int,
        help="Override webhook port from config.",
    )
    serve_webhook.add_argument("--path", help="Override webhook path from config.")
    serve_webhook.add_argument(
        "--once",
        action="store_true",
        help="Handle one request then stop (useful for local testing).",
    )

    report_kpis = subparsers.add_parser(
        "report-kpis",
        help="Phase 5: compute KPIs and produce a weekly markdown report.",
    )
    report_kpis.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    report_kpis.add_argument(
        "--since-days",
        type=int,
        help="Rolling period in days (default: phase5.kpi.rolling_days).",
    )
    report_kpis.add_argument(
        "--output",
        help="Output markdown file path relative to repo root.",
    )
    report_kpis.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print report only without writing a file.",
    )

    diagnose = subparsers.add_parser(
        "diagnose",
        help="Inspect config/repository health and produce a diagnostics markdown report.",
    )
    diagnose.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    diagnose.add_argument(
        "--output",
        help="Output markdown file path relative to repo root.",
    )
    diagnose.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print diagnostics report only without writing a file.",
    )

    dry_run_global = subparsers.add_parser(
        "dry-run-global",
        help="Run bootstrap + event analysis + KPI generation in dry-run mode.",
    )
    dry_run_global.add_argument(
        "--config",
        default=".project-os-agent.yml",
        help="Path to the agent YAML config.",
    )
    dry_run_global.add_argument(
        "--event-name",
        default="",
        help='GitLab event name from header (for example "Merge Request Hook").',
    )
    dry_run_payload_group = dry_run_global.add_mutually_exclusive_group()
    dry_run_payload_group.add_argument(
        "--payload-file",
        help="Read JSON payload from file.",
    )
    dry_run_payload_group.add_argument(
        "--payload-json",
        help="Read JSON payload from a command-line string.",
    )
    dry_run_global.add_argument(
        "--since-days",
        type=int,
        help="Rolling period in days used for KPI dry-run report.",
    )
    dry_run_global.add_argument(
        "--no-kpi-markdown",
        action="store_true",
        help="Hide KPI markdown block and print JSON summary only.",
    )

    return parser.parse_args(normalized_argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    command = args.command or "bootstrap"

    try:
        if command == "bootstrap":
            return run_bootstrap(args)
        if command == "process-event":
            return run_process_event(args)
        if command == "sync-docs":
            return run_sync_docs(args)
        if command == "act":
            return run_act(args)
        if command == "serve-webhook":
            return run_serve_webhook(args)
        if command == "report-kpis":
            return run_report_kpis(args)
        if command == "diagnose":
            return run_diagnose(args)
        if command == "dry-run-global":
            return run_dry_run_global(args)
        raise ValueError(f"Unsupported command: {command}")
    except Exception as exc:  # noqa: BLE001
        print(f"[project-os-agent] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

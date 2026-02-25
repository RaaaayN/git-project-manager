from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import read_audit_events
from .config import AgentConfig, _is_within, load_config
from .guardrails import assert_content_is_safe, assert_write_targets_allowed


def _status_label(ok: bool) -> str:
    return "OK" if ok else "WARN"


def _build_diagnostic_metrics(repo_root: Path, config: AgentConfig) -> dict[str, Any]:
    templates_dir = (repo_root / config.templates_dir).resolve()
    managed_targets = [mapping.target for mapping in config.managed_files]
    missing_targets = [target for target in managed_targets if not (repo_root / target).exists()]

    audit_log_path = (repo_root / config.phase5.audit.log_file).resolve()
    audit_events = read_audit_events(repo_root, config)
    token_env_name = config.gitlab.token_env
    gitlab_token_set = bool(os.getenv(token_env_name, "").strip())

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "templates_dir": {
            "path": config.templates_dir,
            "exists": templates_dir.exists(),
        },
        "managed_docs": {
            "total": len(managed_targets),
            "present": len(managed_targets) - len(missing_targets),
            "missing": missing_targets,
        },
        "audit": {
            "enabled": bool(config.phase5.enabled and config.phase5.audit.enabled),
            "log_file": config.phase5.audit.log_file,
            "log_exists": audit_log_path.exists(),
            "events_count": len(audit_events),
        },
        "gitlab": {
            "enabled": config.gitlab.enabled,
            "api_url": config.gitlab.api_url,
            "project_id": config.gitlab.project_id,
            "token_env": token_env_name,
            "token_is_set": gitlab_token_set,
        },
    }


def _build_diagnostic_markdown(metrics: dict[str, Any]) -> str:
    templates = metrics.get("templates_dir", {})
    docs = metrics.get("managed_docs", {})
    audit = metrics.get("audit", {})
    gitlab = metrics.get("gitlab", {})
    missing_docs = docs.get("missing", [])
    if not isinstance(missing_docs, list):
        missing_docs = []

    lines = [
        "# Project OS Agent CLI Diagnostics",
        "",
        f"Generated at: {metrics.get('generated_at', 'n/a')}",
        "",
        "## Summary",
        "",
        f"- templates_status: {_status_label(bool(templates.get('exists')))}",
        f"- managed_docs_status: {_status_label(not missing_docs)}",
        f"- audit_status: {_status_label(bool(audit.get('enabled')))}",
        f"- gitlab_status: {_status_label(bool(gitlab.get('enabled')))}",
        "",
        "## Managed Documentation",
        "",
        f"- total: {docs.get('total', 0)}",
        f"- present: {docs.get('present', 0)}",
        f"- missing_count: {len(missing_docs)}",
    ]
    for target in missing_docs:
        lines.append(f"- missing: {target}")

    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"- enabled: {audit.get('enabled', False)}",
            f"- log_file: {audit.get('log_file', 'n/a')}",
            f"- log_exists: {audit.get('log_exists', False)}",
            f"- events_count: {audit.get('events_count', 0)}",
            "",
            "## GitLab",
            "",
            f"- enabled: {gitlab.get('enabled', False)}",
            f"- api_url: {gitlab.get('api_url', '')}",
            f"- project_id: {gitlab.get('project_id', '') or 'n/a'}",
            f"- token_env: {gitlab.get('token_env', '')}",
            f"- token_is_set: {gitlab.get('token_is_set', False)}",
            "",
            "## Raw Metrics (JSON)",
            "",
            "```json",
            json.dumps(metrics, indent=2, ensure_ascii=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_output_path(repo_root: Path, output: str | None) -> Path:
    if output:
        target = (repo_root / output).resolve()
    else:
        target = (repo_root / ".project-os-agent/reports/diagnostics.md").resolve()
    if not _is_within(repo_root, target):
        raise ValueError("diagnostics output path points outside repository")
    return target


def run_diagnose(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()

    metrics = _build_diagnostic_metrics(repo_root, config)
    report_markdown = _build_diagnostic_markdown(metrics)
    if args.stdout_only:
        print(report_markdown)
        return 0

    report_path = _resolve_output_path(repo_root, args.output)
    report_relpath = report_path.relative_to(repo_root).as_posix()
    assert_write_targets_allowed(
        repo_root,
        config,
        [report_relpath],
        extra_allowed_paths=[report_relpath],
    )
    assert_content_is_safe(config, report_relpath, report_markdown)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_markdown, encoding="utf-8")
    print(f"[project-os-agent] Diagnostic report written: {report_relpath}")
    print(report_markdown)
    return 0


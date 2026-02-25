from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit import log_audit_event, read_audit_events
from .cli_validation import resolve_since_days
from .config import AgentConfig, _is_within, load_config, safe_text
from .guardrails import assert_content_is_safe, assert_write_targets_allowed


def _to_iso_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


def _parse_timestamp(raw_value: Any) -> datetime | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _compute_docs_freshness(repo_root: Path, config: AgentConfig, now_utc: datetime) -> dict[str, Any]:
    threshold = now_utc - timedelta(days=config.phase3.stale_days)
    total = len(config.managed_files)
    fresh = 0
    stale = 0
    missing = 0

    for mapping in config.managed_files:
        path = (repo_root / mapping.target).resolve()
        if not path.exists():
            missing += 1
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified >= threshold:
            fresh += 1
        else:
            stale += 1

    ratio = (fresh / total) if total else 1.0
    return {
        "total": total,
        "fresh": fresh,
        "stale": stale,
        "missing": missing,
        "ratio": round(ratio, 4),
        "stale_threshold_days": config.phase3.stale_days,
    }


def _compute_pipeline_green_streak(pipeline_states: list[str]) -> int:
    streak = 0
    for state in reversed(pipeline_states):
        lowered = state.lower().strip()
        if lowered in {"success", "passed"}:
            streak += 1
            continue
        break
    return streak


def _sum_int(values: list[Any]) -> int:
    total = 0
    for value in values:
        if isinstance(value, int):
            total += value
    return total


def _compute_kpis(
    repo_root: Path,
    config: AgentConfig,
    events: list[dict[str, Any]],
    now_utc: datetime,
) -> dict[str, Any]:
    docs_freshness = _compute_docs_freshness(repo_root, config, now_utc)

    command_events = [item for item in events if safe_text(item.get("category")) == "command"]
    operation_events = [item for item in events if safe_text(item.get("category")) == "operation"]

    response_durations_ms: list[int] = []
    blocked_actions = 0
    pipeline_states: list[str] = []
    docs_updated_events = 0

    for event in command_events:
        command_name = safe_text(event.get("command"))
        duration = event.get("duration_ms")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}

        if command_name in {"process-event", "sync-docs", "act", "webhook"} and isinstance(duration, int):
            response_durations_ms.append(duration)

        blocked_actions += _sum_int([details.get("blocked_actions_count")])
        docs_updated_events += _sum_int([details.get("updated_count"), details.get("created_count")])

        event_type = safe_text(event.get("event_type"))
        event_state = safe_text(details.get("event_state"))
        if event_type == "pipeline" and event_state:
            pipeline_states.append(event_state)

    mean_response_time_seconds = (
        round((sum(response_durations_ms) / len(response_durations_ms)) / 1000, 3)
        if response_durations_ms
        else None
    )

    open_mr_operations: list[dict[str, Any]] = []
    for item in operation_events:
        details = item.get("details")
        if not isinstance(details, dict):
            continue
        if safe_text(details.get("op_type")) == "open_merge_request":
            open_mr_operations.append(item)
    mr_total = len(open_mr_operations)
    mr_success = 0
    for item in open_mr_operations:
        status = safe_text(item.get("status"))
        if status in {"applied", "dry_run", "skipped_gitlab_disabled"}:
            mr_success += 1

    pipeline_failures = sum(
        1
        for state in pipeline_states
        if state.lower().strip() in {"failed", "canceled", "error"}
    )

    return {
        "docs_freshness_ratio": docs_freshness["ratio"],
        "docs_freshness": docs_freshness,
        "mean_response_time_seconds": mean_response_time_seconds,
        "events_count": len(events),
        "commands_count": len(command_events),
        "operations_count": len(operation_events),
        "blocked_actions_count": blocked_actions,
        "docs_update_actions": docs_updated_events,
        "mr_open_operations_total": mr_total,
        "mr_open_success_ratio": round((mr_success / mr_total), 4) if mr_total else None,
        "pipeline_failure_events": pipeline_failures,
        "pipeline_green_streak": _compute_pipeline_green_streak(pipeline_states),
    }


def _build_markdown_report(
    *,
    period_start: datetime,
    period_end: datetime,
    metrics: dict[str, Any],
) -> str:
    docs_freshness = metrics.get("docs_freshness", {}) if isinstance(metrics.get("docs_freshness"), dict) else {}
    ratio = metrics.get("docs_freshness_ratio")
    response_time = metrics.get("mean_response_time_seconds")
    mr_ratio = metrics.get("mr_open_success_ratio")

    lines = [
        "# Project OS Agent Weekly KPI Report",
        "",
        f"Period: {_to_iso_date(period_start)} -> {_to_iso_date(period_end)} (UTC)",
        "",
        "## Core KPIs",
        "",
        f"- docs_freshness_ratio: {ratio if ratio is not None else 'n/a'}",
        (
            f"- mean_response_time: {response_time}s"
            if response_time is not None
            else "- mean_response_time: n/a"
        ),
        (
            f"- mr_open_success_ratio: {mr_ratio}"
            if mr_ratio is not None
            else "- mr_open_success_ratio: n/a"
        ),
        f"- blocked_actions_count: {metrics.get('blocked_actions_count', 0)}",
        f"- pipeline_failure_events: {metrics.get('pipeline_failure_events', 0)}",
        f"- pipeline_green_streak: {metrics.get('pipeline_green_streak', 0)}",
        "",
        "## Docs Freshness Detail",
        "",
        f"- total_managed_docs: {docs_freshness.get('total', 0)}",
        f"- fresh_docs: {docs_freshness.get('fresh', 0)}",
        f"- stale_docs: {docs_freshness.get('stale', 0)}",
        f"- missing_docs: {docs_freshness.get('missing', 0)}",
        f"- stale_threshold_days: {docs_freshness.get('stale_threshold_days', 0)}",
        "",
        "## Activity Summary",
        "",
        f"- audit_events: {metrics.get('events_count', 0)}",
        f"- command_events: {metrics.get('commands_count', 0)}",
        f"- operation_events: {metrics.get('operations_count', 0)}",
        f"- docs_update_actions: {metrics.get('docs_update_actions', 0)}",
        "",
        "## Raw Metrics (JSON)",
        "",
        "```json",
        json.dumps(metrics, indent=2, ensure_ascii=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def _resolve_report_path(
    repo_root: Path,
    config: AgentConfig,
    report_end: datetime,
    output: str | None,
) -> Path:
    if output:
        target = (repo_root / output).resolve()
    else:
        filename = f"{config.phase5.kpi.report_prefix}-{report_end.date().isoformat()}.md"
        target = (repo_root / config.phase5.kpi.report_dir / filename).resolve()

    if not _is_within(repo_root, target):
        raise ValueError("KPI report output path points outside repository")
    return target


def generate_kpi_report(
    repo_root: Path,
    config: AgentConfig,
    *,
    since_days: int,
    output: str | None,
    write: bool,
) -> tuple[dict[str, Any], str, str | None]:
    now_utc = datetime.now(timezone.utc)
    period_start = now_utc - timedelta(days=since_days)

    events = read_audit_events(repo_root, config, since_utc=period_start)
    metrics = _compute_kpis(repo_root, config, events, now_utc)
    report_markdown = _build_markdown_report(
        period_start=period_start,
        period_end=now_utc,
        metrics=metrics,
    )

    report_relpath: str | None = None
    if write:
        report_path = _resolve_report_path(repo_root, config, now_utc, output)
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

    return metrics, report_markdown, report_relpath


def run_report_kpis(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()

    since_days = resolve_since_days(args.since_days, config.phase5.kpi.rolling_days)

    started = datetime.now(timezone.utc)
    metrics, report_markdown, report_relpath = generate_kpi_report(
        repo_root,
        config,
        since_days=since_days,
        output=args.output,
        write=not args.stdout_only,
    )
    finished = datetime.now(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)

    if report_relpath:
        print(f"[project-os-agent] KPI report written: {report_relpath}")
    print(report_markdown)

    log_audit_event(
        repo_root,
        config,
        "command",
        "success",
        command="report-kpis",
        duration_ms=duration_ms,
        details={
            "since_days": since_days,
            "output": report_relpath or "stdout-only",
            "metrics": metrics,
        },
    )
    return 0

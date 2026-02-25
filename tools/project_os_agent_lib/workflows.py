from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .cli_validation import resolve_since_days
from .config import load_config
from .guardrails import enforce_rate_limit, payload_size_from_json, validate_payload_size
from .kpi import generate_kpi_report
from .pipeline import _infer_event_name_from_payload, _read_payload, create_missing_files, process_event_pipeline


def run_dry_run_global(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()

    payload = _read_payload(args)
    validate_payload_size(config, payload_size_from_json(payload), "dry-run-global")
    enforce_rate_limit(repo_root, config, "dry-run-global")
    event_name = args.event_name or _infer_event_name_from_payload(payload)

    bootstrap_results = create_missing_files(repo_root, config, dry_run=True)
    event_plan = process_event_pipeline(repo_root, config, event_name, payload)
    since_days = resolve_since_days(args.since_days, config.phase5.kpi.rolling_days)
    metrics, report_markdown, _ = generate_kpi_report(
        repo_root,
        config,
        since_days=since_days,
        output=None,
        write=False,
    )

    summary: dict[str, Any] = {
        "mode": "dry-run-global",
        "bootstrap": {
            "would_create_count": sum(1 for item in bootstrap_results if item.status == "would_create"),
            "skipped_existing_count": sum(
                1 for item in bootstrap_results if item.status == "skipped_existing"
            ),
            "blocked_guardrail_count": sum(
                1 for item in bootstrap_results if item.status == "blocked_guardrail"
            ),
        },
        "event": {
            "event_name": event_plan.normalized_event.event_name,
            "event_type": event_plan.normalized_event.event_type,
            "reference": event_plan.normalized_event.reference,
            "gaps_count": len(event_plan.gaps),
            "allowed_actions_count": len(event_plan.allowed_actions),
            "blocked_actions_count": len(event_plan.blocked_actions),
            "next_steps_count": len(event_plan.next_steps),
        },
        "kpis": {
            "since_days": since_days,
            "docs_freshness_ratio": metrics.get("docs_freshness_ratio"),
            "mean_response_time_seconds": metrics.get("mean_response_time_seconds"),
            "pipeline_green_streak": metrics.get("pipeline_green_streak"),
        },
    }

    print(json.dumps(summary, indent=2, ensure_ascii=True))
    if not args.no_kpi_markdown:
        print("")
        print(report_markdown)
    return 0


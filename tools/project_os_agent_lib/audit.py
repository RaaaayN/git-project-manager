from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AgentConfig, _is_within, safe_text


MAX_LIST_ITEMS = 50
MAX_DICT_ITEMS = 50


def _truncate_string(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}... [truncated {len(value) - max_chars} chars]"


def _sanitize_value(
    value: Any,
    redact_keys: list[str],
    max_entry_chars: int,
    current_key: str = "",
) -> Any:
    key_lower = current_key.lower()
    if any(token in key_lower for token in redact_keys):
        return "[REDACTED]"

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                sanitized["_truncated_keys"] = f"{len(value) - MAX_DICT_ITEMS}"
                break
            safe_key = safe_text(key)
            sanitized[safe_key] = _sanitize_value(
                child,
                redact_keys,
                max_entry_chars,
                safe_key,
            )
        return sanitized

    if isinstance(value, list):
        sanitized_list: list[Any] = []
        for index, item in enumerate(value):
            if index >= MAX_LIST_ITEMS:
                sanitized_list.append(f"[TRUNCATED: {len(value) - MAX_LIST_ITEMS} items omitted]")
                break
            sanitized_list.append(_sanitize_value(item, redact_keys, max_entry_chars, current_key))
        return sanitized_list

    if isinstance(value, str):
        return _truncate_string(value, max_entry_chars)

    if value is None or isinstance(value, (int, float, bool)):
        return value

    return _truncate_string(safe_text(value), max_entry_chars)


def _resolve_audit_log_path(repo_root: Path, config: AgentConfig) -> Path:
    log_path = (repo_root / config.phase5.audit.log_file).resolve()
    if not _is_within(repo_root, log_path):
        raise ValueError("phase5.audit.log_file points outside repository")
    return log_path


def log_audit_event(
    repo_root: Path,
    config: AgentConfig,
    category: str,
    status: str,
    *,
    command: str = "",
    event_type: str = "",
    reference: str = "",
    duration_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if not config.phase5.enabled or not config.phase5.audit.enabled:
        return

    details_payload = details or {}
    max_chars = config.phase5.audit.max_entry_chars
    redact_keys = [token.lower() for token in config.phase5.audit.redact_keys]

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": safe_text(category),
        "status": safe_text(status),
    }
    if command:
        payload["command"] = safe_text(command)
    if event_type:
        payload["event_type"] = safe_text(event_type)
    if reference:
        payload["reference"] = safe_text(reference)
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if details_payload:
        payload["details"] = _sanitize_value(details_payload, redact_keys, max_chars)

    log_path = _resolve_audit_log_path(repo_root, config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_audit_events(
    repo_root: Path,
    config: AgentConfig,
    *,
    since_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    if not config.phase5.enabled or not config.phase5.audit.enabled:
        return []

    log_path = _resolve_audit_log_path(repo_root, config)
    if not log_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue

        if since_utc is not None:
            raw_timestamp = safe_text(item.get("timestamp"))
            try:
                timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp < since_utc:
                continue

        events.append(item)

    return events

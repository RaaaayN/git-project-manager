from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import AgentConfig, _is_within, safe_text


class GuardrailViolation(RuntimeError):
    """Raised when a phase-5 safety rule is violated."""


def _normalize_repo_path(path_value: str) -> str:
    normalized = Path(path_value).as_posix().strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _resolve_state_path(repo_root: Path, config: AgentConfig) -> Path:
    state_path = (repo_root / config.phase5.rate_limit.state_file).resolve()
    if not _is_within(repo_root, state_path):
        raise GuardrailViolation("phase5.rate_limit.state_file points outside repository")
    return state_path


def safe_write_enabled(config: AgentConfig) -> bool:
    return bool(config.phase5.enabled and config.phase5.guardrails.enabled and config.phase5.guardrails.safe_write)


def enforce_rate_limit(repo_root: Path, config: AgentConfig, bucket: str) -> tuple[int, int]:
    if not config.phase5.enabled or not config.phase5.rate_limit.enabled:
        return 0, 0

    state_path = _resolve_state_path(repo_root, config)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except json.JSONDecodeError:
        raw_state = {}

    if not isinstance(raw_state, dict):
        raw_state = {}

    now = time.time()
    window_start = now - float(config.phase5.rate_limit.window_seconds)
    bucket_key = safe_text(bucket) or "default"

    entries_raw = raw_state.get(bucket_key, [])
    if not isinstance(entries_raw, list):
        entries_raw = []

    entries: list[float] = []
    for item in entries_raw:
        if isinstance(item, (int, float)) and float(item) >= window_start:
            entries.append(float(item))

    if len(entries) >= config.phase5.rate_limit.max_events:
        raise GuardrailViolation(
            f"Rate limit exceeded for '{bucket_key}' ({len(entries)}/{config.phase5.rate_limit.max_events} "
            f"in {config.phase5.rate_limit.window_seconds}s)"
        )

    entries.append(now)
    raw_state[bucket_key] = entries
    state_path.write_text(json.dumps(raw_state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return len(entries), config.phase5.rate_limit.max_events


def validate_payload_size(config: AgentConfig, payload_size_bytes: int, context_label: str) -> None:
    if not config.phase5.enabled or not config.phase5.guardrails.enabled:
        return
    if payload_size_bytes <= config.phase5.guardrails.max_payload_bytes:
        return

    raise GuardrailViolation(
        f"Payload too large for {context_label}: {payload_size_bytes} bytes "
        f"(max: {config.phase5.guardrails.max_payload_bytes})"
    )


def assert_write_targets_allowed(
    repo_root: Path,
    config: AgentConfig,
    targets: list[str],
    *,
    extra_allowed_paths: list[str] | None = None,
) -> None:
    if not config.phase5.enabled or not config.phase5.guardrails.enabled:
        return

    configured_allowed = {
        _normalize_repo_path(path)
        for path in config.phase5.guardrails.allowed_write_paths
        if _normalize_repo_path(path)
    }
    if configured_allowed:
        allowed_paths = configured_allowed
    else:
        allowed_paths = {
            _normalize_repo_path(mapping.target)
            for mapping in config.managed_files
            if _normalize_repo_path(mapping.target)
        }

    if extra_allowed_paths:
        allowed_paths.update(
            _normalize_repo_path(path)
            for path in extra_allowed_paths
            if _normalize_repo_path(path)
        )

    blocked_prefixes = [
        _normalize_repo_path(prefix)
        for prefix in config.phase5.guardrails.blocked_path_prefixes
        if _normalize_repo_path(prefix)
    ]

    for target in targets:
        normalized = _normalize_repo_path(target)
        if not normalized:
            raise GuardrailViolation("Write target cannot be empty")

        candidate_path = (repo_root / normalized).resolve()
        if not _is_within(repo_root, candidate_path):
            raise GuardrailViolation(f"Write target escapes repository: {target}")

        for blocked_prefix in blocked_prefixes:
            blocked_root = blocked_prefix.rstrip("/")
            if normalized == blocked_root or normalized.startswith(f"{blocked_root}/"):
                raise GuardrailViolation(
                    f"Write target is blocked by guardrails: {target} (prefix {blocked_prefix})"
                )

        if normalized not in allowed_paths:
            raise GuardrailViolation(f"Write target is not in allowed set: {target}")


def assert_content_is_safe(config: AgentConfig, target: str, content: str) -> None:
    if not config.phase5.enabled or not config.phase5.guardrails.enabled:
        return

    for raw_pattern in config.phase5.guardrails.secret_patterns:
        try:
            pattern = re.compile(raw_pattern)
        except re.error as exc:
            raise GuardrailViolation(f"Invalid guardrail regex pattern '{raw_pattern}': {exc}") from exc

        match = pattern.search(content)
        if not match:
            continue

        matched = match.group(0).strip().replace("\n", " ")
        if len(matched) > 80:
            matched = f"{matched[:80]}..."
        raise GuardrailViolation(
            f"Secret-like content detected in {target} by guardrail pattern {raw_pattern}: {matched}"
        )


def payload_size_from_json(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))

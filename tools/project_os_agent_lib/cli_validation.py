from __future__ import annotations

from pathlib import Path

from .config import WebhookConfig


def resolve_config_path(config_value: str) -> Path:
    if not isinstance(config_value, str) or not config_value.strip():
        raise ValueError("--config must be a non-empty path")
    return Path(config_value).resolve()


def resolve_since_days(since_days: int | None, default_days: int) -> int:
    value = since_days if since_days is not None else default_days
    if value < 1:
        raise ValueError("since-days must be >= 1")
    return value


def resolve_webhook_settings(
    config: WebhookConfig,
    *,
    host_override: str | None,
    port_override: int | None,
    path_override: str | None,
) -> tuple[str, int, str]:
    host = config.host if host_override is None else host_override
    port = port_override if port_override is not None else config.port
    path = config.path if path_override is None else path_override

    if not isinstance(host, str) or not host.strip():
        raise ValueError("webhook host must be a non-empty string")
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise ValueError("webhook port must be an integer between 1 and 65535")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("webhook path must be a non-empty string")

    if not path.startswith("/"):
        path = f"/{path}"
    return host.strip(), port, path


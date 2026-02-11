from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


KNOWN_ACTIONS = {
    "open_merge_request",
    "create_issue",
    "comment_issue",
    "update_documentation",
    "write_adr",
}

CRITICAL_DOCS = {
    "PRODUCT_SPEC.md",
    "PROJECT_STATUS.md",
    "AGENTS.md",
    "CLAUDE.md",
    "DECISIONS.md",
}


@dataclass(frozen=True)
class ManagedFile:
    template: str
    target: str


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool
    host: str
    port: int
    path: str
    secret_env: str


@dataclass(frozen=True)
class PolicyConfig:
    allowed_actions: set[str]
    forbidden_actions: set[str]


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    provider: str
    model: str
    api_key_env: str
    timeout_seconds: int


@dataclass(frozen=True)
class MCPConfig:
    enabled: bool
    context_file: str


@dataclass(frozen=True)
class Phase3Config:
    stale_days: int
    next_steps_default_owner: str
    llm: LLMConfig
    mcp: MCPConfig


@dataclass(frozen=True)
class Phase4Config:
    enabled: bool
    auto_sync_docs: bool
    mr_title_prefix: str
    issue_title_prefix: str
    comment_on_source_event: bool


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool
    log_file: str
    redact_keys: list[str]
    max_entry_chars: int


@dataclass(frozen=True)
class GuardrailsConfig:
    enabled: bool
    safe_write: bool
    allowed_write_paths: list[str]
    blocked_path_prefixes: list[str]
    secret_patterns: list[str]
    max_payload_bytes: int


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool
    state_file: str
    window_seconds: int
    max_events: int


@dataclass(frozen=True)
class KPIConfig:
    enabled: bool
    report_dir: str
    report_prefix: str
    rolling_days: int


@dataclass(frozen=True)
class Phase5Config:
    enabled: bool
    audit: AuditConfig
    guardrails: GuardrailsConfig
    rate_limit: RateLimitConfig
    kpi: KPIConfig


@dataclass(frozen=True)
class GitLabConfig:
    enabled: bool
    api_url: str
    project_id: str
    token_env: str
    target_branch: str
    branch_prefix: str
    labels: list[str]


@dataclass
class AgentConfig:
    version: int
    templates_dir: str
    dry_run: bool
    managed_files: list[ManagedFile]
    webhook: WebhookConfig
    policy: PolicyConfig
    phase3: Phase3Config
    phase4: Phase4Config
    phase5: Phase5Config
    gitlab: GitLabConfig
    placeholders: dict[str, str] = field(default_factory=dict)


@dataclass
class ActionResult:
    target: str
    status: str
    diff: str | None = None


@dataclass(frozen=True)
class NormalizedEvent:
    event_name: str
    event_type: str
    action: str
    state: str
    title: str
    author: str
    url: str
    project: str
    reference: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Gap:
    code: str
    severity: str
    summary: str
    evidence: list[str]


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    summary: str
    target: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BlockedAction:
    action: ProposedAction
    reason: str


@dataclass(frozen=True)
class EventPlan:
    normalized_event: NormalizedEvent
    context: dict[str, Any]
    gaps: list[Gap]
    proposed_actions: list[ProposedAction]
    allowed_actions: list[ProposedAction]
    blocked_actions: list[BlockedAction]
    next_steps: list[dict[str, str]]
    next_steps_source: str
    adr_draft: dict[str, Any] | None


@dataclass(frozen=True)
class ActorOperation:
    op_type: str
    action_type: str
    payload: dict[str, Any]


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _ensure_yaml_object(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be an object")
    return value


def _normalize_relative_path(path_value: str, field_name: str) -> str:
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string")
    if Path(path_value).is_absolute():
        raise ValueError(f"'{field_name}' must be a relative path")
    return path_value


def safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _validate_mapping(raw_mapping: dict[str, Any], index: int) -> ManagedFile:
    template = raw_mapping.get("template")
    target = raw_mapping.get("target", template)

    template = _normalize_relative_path(template, f"managed_files[{index}].template")
    target = _normalize_relative_path(target, f"managed_files[{index}].target")

    return ManagedFile(template=template, target=target)


def _validate_webhook(raw_webhook: dict[str, Any]) -> WebhookConfig:
    enabled = raw_webhook.get("enabled", True)
    host = raw_webhook.get("host", "0.0.0.0")
    port = raw_webhook.get("port", 8080)
    path = raw_webhook.get("path", "/webhooks/gitlab")
    secret_env = raw_webhook.get("secret_env", "GITLAB_WEBHOOK_SECRET")

    if not isinstance(enabled, bool):
        raise ValueError("'webhook.enabled' must be a boolean")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("'webhook.host' must be a non-empty string")
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise ValueError("'webhook.port' must be an integer between 1 and 65535")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("'webhook.path' must be a non-empty string")
    if not path.startswith("/"):
        path = f"/{path}"
    if not isinstance(secret_env, str) or not secret_env.strip():
        raise ValueError("'webhook.secret_env' must be a non-empty string")

    return WebhookConfig(
        enabled=enabled,
        host=host,
        port=port,
        path=path,
        secret_env=secret_env,
    )


def _validate_policy(raw_policy: dict[str, Any]) -> PolicyConfig:
    allowed_raw = raw_policy.get("allowed_actions", sorted(KNOWN_ACTIONS))
    forbidden_raw = raw_policy.get("forbidden_actions", [])

    if not isinstance(allowed_raw, list):
        raise ValueError("'policy.allowed_actions' must be an array")
    if not isinstance(forbidden_raw, list):
        raise ValueError("'policy.forbidden_actions' must be an array")

    allowed_actions: set[str] = set()
    forbidden_actions: set[str] = set()

    for action in allowed_raw:
        if not isinstance(action, str) or not action.strip():
            raise ValueError("'policy.allowed_actions' must contain non-empty strings")
        allowed_actions.add(action)

    for action in forbidden_raw:
        if not isinstance(action, str) or not action.strip():
            raise ValueError("'policy.forbidden_actions' must contain non-empty strings")
        forbidden_actions.add(action)

    unknown_allowed_actions = allowed_actions - KNOWN_ACTIONS
    if unknown_allowed_actions:
        unknown_csv = ", ".join(sorted(unknown_allowed_actions))
        raise ValueError(f"Unknown action type(s) in policy.allowed_actions: {unknown_csv}")

    return PolicyConfig(
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
    )


def _validate_llm(raw_llm: dict[str, Any]) -> LLMConfig:
    enabled = raw_llm.get("enabled", False)
    provider = raw_llm.get("provider", "gemini")
    model = raw_llm.get("model", "gemini-2.0-flash")
    api_key_env = raw_llm.get("api_key_env", "GEMINI_API_KEY")
    timeout_seconds = raw_llm.get("timeout_seconds", 20)

    if not isinstance(enabled, bool):
        raise ValueError("'phase3.llm.enabled' must be a boolean")
    if not isinstance(provider, str) or provider.strip() != "gemini":
        raise ValueError("'phase3.llm.provider' must be 'gemini'")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("'phase3.llm.model' must be a non-empty string")
    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ValueError("'phase3.llm.api_key_env' must be a non-empty string")
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1 or timeout_seconds > 120:
        raise ValueError("'phase3.llm.timeout_seconds' must be an integer between 1 and 120")

    return LLMConfig(
        enabled=enabled,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
    )


def _validate_mcp(raw_mcp: dict[str, Any]) -> MCPConfig:
    enabled = raw_mcp.get("enabled", False)
    context_file = raw_mcp.get("context_file", ".mcp/context.json")

    if not isinstance(enabled, bool):
        raise ValueError("'phase3.mcp.enabled' must be a boolean")
    context_file = _normalize_relative_path(context_file, "phase3.mcp.context_file")

    return MCPConfig(
        enabled=enabled,
        context_file=context_file,
    )


def _validate_phase3(raw_phase3: dict[str, Any]) -> Phase3Config:
    stale_days = raw_phase3.get("stale_days", 14)
    next_steps_default_owner = raw_phase3.get("next_steps_default_owner", "@team-core")
    llm = _validate_llm(_ensure_yaml_object(raw_phase3.get("llm"), "phase3.llm"))
    mcp = _validate_mcp(_ensure_yaml_object(raw_phase3.get("mcp"), "phase3.mcp"))

    if not isinstance(stale_days, int) or stale_days < 1:
        raise ValueError("'phase3.stale_days' must be an integer >= 1")
    if not isinstance(next_steps_default_owner, str) or not next_steps_default_owner.strip():
        raise ValueError("'phase3.next_steps_default_owner' must be a non-empty string")

    return Phase3Config(
        stale_days=stale_days,
        next_steps_default_owner=next_steps_default_owner,
        llm=llm,
        mcp=mcp,
    )


def _validate_phase4(raw_phase4: dict[str, Any]) -> Phase4Config:
    enabled = raw_phase4.get("enabled", True)
    auto_sync_docs = raw_phase4.get("auto_sync_docs", True)
    mr_title_prefix = raw_phase4.get("mr_title_prefix", "[Agent]")
    issue_title_prefix = raw_phase4.get("issue_title_prefix", "[Agent][Tracking]")
    comment_on_source_event = raw_phase4.get("comment_on_source_event", True)

    if not isinstance(enabled, bool):
        raise ValueError("'phase4.enabled' must be a boolean")
    if not isinstance(auto_sync_docs, bool):
        raise ValueError("'phase4.auto_sync_docs' must be a boolean")
    if not isinstance(mr_title_prefix, str) or not mr_title_prefix.strip():
        raise ValueError("'phase4.mr_title_prefix' must be a non-empty string")
    if not isinstance(issue_title_prefix, str) or not issue_title_prefix.strip():
        raise ValueError("'phase4.issue_title_prefix' must be a non-empty string")
    if not isinstance(comment_on_source_event, bool):
        raise ValueError("'phase4.comment_on_source_event' must be a boolean")

    return Phase4Config(
        enabled=enabled,
        auto_sync_docs=auto_sync_docs,
        mr_title_prefix=mr_title_prefix,
        issue_title_prefix=issue_title_prefix,
        comment_on_source_event=comment_on_source_event,
    )


def _validate_string_list(raw_values: Any, field_name: str) -> list[str]:
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        raise ValueError(f"'{field_name}' must be an array")

    values: list[str] = []
    for item in raw_values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"'{field_name}' must contain non-empty strings")
        values.append(item.strip())
    return values


def _validate_audit(raw_audit: dict[str, Any]) -> AuditConfig:
    enabled = raw_audit.get("enabled", True)
    log_file = raw_audit.get("log_file", ".project-os-agent/audit.log.jsonl")
    redact_keys = raw_audit.get(
        "redact_keys",
        ["token", "authorization", "private_token", "api_key", "secret"],
    )
    max_entry_chars = raw_audit.get("max_entry_chars", 2000)

    if not isinstance(enabled, bool):
        raise ValueError("'phase5.audit.enabled' must be a boolean")
    log_file = _normalize_relative_path(log_file, "phase5.audit.log_file")
    redact_keys = _validate_string_list(redact_keys, "phase5.audit.redact_keys")
    if not isinstance(max_entry_chars, int) or max_entry_chars < 256:
        raise ValueError("'phase5.audit.max_entry_chars' must be an integer >= 256")

    return AuditConfig(
        enabled=enabled,
        log_file=log_file,
        redact_keys=redact_keys,
        max_entry_chars=max_entry_chars,
    )


def _validate_guardrails(raw_guardrails: dict[str, Any]) -> GuardrailsConfig:
    enabled = raw_guardrails.get("enabled", True)
    safe_write = raw_guardrails.get("safe_write", True)
    allowed_write_paths = raw_guardrails.get("allowed_write_paths", [])
    blocked_path_prefixes = raw_guardrails.get(
        "blocked_path_prefixes",
        [".git/", ".env", "secrets/", ".mcp/secrets/"],
    )
    secret_patterns = raw_guardrails.get(
        "secret_patterns",
        [
            "(?i)glpat-[A-Za-z0-9\\-_]{16,}",
            "(?i)AIza[0-9A-Za-z\\-_]{35}",
            "(?i)(api[_-]?key|token|secret)\\s*[:=]\\s*[\\\"']?[A-Za-z0-9_\\-]{12,}",
        ],
    )
    max_payload_bytes = raw_guardrails.get("max_payload_bytes", 524288)

    if not isinstance(enabled, bool):
        raise ValueError("'phase5.guardrails.enabled' must be a boolean")
    if not isinstance(safe_write, bool):
        raise ValueError("'phase5.guardrails.safe_write' must be a boolean")
    allowed_write_paths = _validate_string_list(
        allowed_write_paths,
        "phase5.guardrails.allowed_write_paths",
    )
    blocked_path_prefixes = _validate_string_list(
        blocked_path_prefixes,
        "phase5.guardrails.blocked_path_prefixes",
    )
    secret_patterns = _validate_string_list(secret_patterns, "phase5.guardrails.secret_patterns")
    if not isinstance(max_payload_bytes, int) or max_payload_bytes < 1024:
        raise ValueError("'phase5.guardrails.max_payload_bytes' must be an integer >= 1024")

    return GuardrailsConfig(
        enabled=enabled,
        safe_write=safe_write,
        allowed_write_paths=allowed_write_paths,
        blocked_path_prefixes=blocked_path_prefixes,
        secret_patterns=secret_patterns,
        max_payload_bytes=max_payload_bytes,
    )


def _validate_rate_limit(raw_rate_limit: dict[str, Any]) -> RateLimitConfig:
    enabled = raw_rate_limit.get("enabled", True)
    state_file = raw_rate_limit.get("state_file", ".project-os-agent/rate-limit.json")
    window_seconds = raw_rate_limit.get("window_seconds", 60)
    max_events = raw_rate_limit.get("max_events", 30)

    if not isinstance(enabled, bool):
        raise ValueError("'phase5.rate_limit.enabled' must be a boolean")
    state_file = _normalize_relative_path(state_file, "phase5.rate_limit.state_file")
    if not isinstance(window_seconds, int) or window_seconds < 1:
        raise ValueError("'phase5.rate_limit.window_seconds' must be an integer >= 1")
    if not isinstance(max_events, int) or max_events < 1:
        raise ValueError("'phase5.rate_limit.max_events' must be an integer >= 1")

    return RateLimitConfig(
        enabled=enabled,
        state_file=state_file,
        window_seconds=window_seconds,
        max_events=max_events,
    )


def _validate_kpi(raw_kpi: dict[str, Any]) -> KPIConfig:
    enabled = raw_kpi.get("enabled", True)
    report_dir = raw_kpi.get("report_dir", ".project-os-agent/reports")
    report_prefix = raw_kpi.get("report_prefix", "weekly-kpi")
    rolling_days = raw_kpi.get("rolling_days", 7)

    if not isinstance(enabled, bool):
        raise ValueError("'phase5.kpi.enabled' must be a boolean")
    report_dir = _normalize_relative_path(report_dir, "phase5.kpi.report_dir")
    if not isinstance(report_prefix, str) or not report_prefix.strip():
        raise ValueError("'phase5.kpi.report_prefix' must be a non-empty string")
    if not isinstance(rolling_days, int) or rolling_days < 1:
        raise ValueError("'phase5.kpi.rolling_days' must be an integer >= 1")

    return KPIConfig(
        enabled=enabled,
        report_dir=report_dir,
        report_prefix=report_prefix.strip(),
        rolling_days=rolling_days,
    )


def _validate_phase5(raw_phase5: dict[str, Any]) -> Phase5Config:
    enabled = raw_phase5.get("enabled", True)
    audit = _validate_audit(_ensure_yaml_object(raw_phase5.get("audit"), "phase5.audit"))
    guardrails = _validate_guardrails(
        _ensure_yaml_object(raw_phase5.get("guardrails"), "phase5.guardrails")
    )
    rate_limit = _validate_rate_limit(
        _ensure_yaml_object(raw_phase5.get("rate_limit"), "phase5.rate_limit")
    )
    kpi = _validate_kpi(_ensure_yaml_object(raw_phase5.get("kpi"), "phase5.kpi"))

    if not isinstance(enabled, bool):
        raise ValueError("'phase5.enabled' must be a boolean")

    return Phase5Config(
        enabled=enabled,
        audit=audit,
        guardrails=guardrails,
        rate_limit=rate_limit,
        kpi=kpi,
    )


def _validate_gitlab(raw_gitlab: dict[str, Any]) -> GitLabConfig:
    enabled = raw_gitlab.get("enabled", False)
    api_url = raw_gitlab.get("api_url", "https://gitlab.com/api/v4")
    project_id = raw_gitlab.get("project_id", "")
    token_env = raw_gitlab.get("token_env", "GITLAB_TOKEN")
    target_branch = raw_gitlab.get("target_branch", "main")
    branch_prefix = raw_gitlab.get("branch_prefix", "project-os-agent")
    labels_raw = raw_gitlab.get("labels", [])

    if not isinstance(enabled, bool):
        raise ValueError("'gitlab.enabled' must be a boolean")
    if not isinstance(api_url, str) or not api_url.strip():
        raise ValueError("'gitlab.api_url' must be a non-empty string")
    if not isinstance(project_id, str):
        raise ValueError("'gitlab.project_id' must be a string")
    if not isinstance(token_env, str) or not token_env.strip():
        raise ValueError("'gitlab.token_env' must be a non-empty string")
    if not isinstance(target_branch, str) or not target_branch.strip():
        raise ValueError("'gitlab.target_branch' must be a non-empty string")
    if not isinstance(branch_prefix, str) or not branch_prefix.strip():
        raise ValueError("'gitlab.branch_prefix' must be a non-empty string")
    if not isinstance(labels_raw, list):
        raise ValueError("'gitlab.labels' must be an array")

    labels: list[str] = []
    for label in labels_raw:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("'gitlab.labels' must contain non-empty strings")
        labels.append(label.strip())

    if enabled and not project_id.strip():
        raise ValueError("'gitlab.project_id' is required when gitlab.enabled=true")

    return GitLabConfig(
        enabled=enabled,
        api_url=api_url.rstrip("/"),
        project_id=project_id.strip(),
        token_env=token_env,
        target_branch=target_branch.strip(),
        branch_prefix=branch_prefix.strip().strip("/"),
        labels=labels,
    )


def load_config(config_path: Path) -> AgentConfig:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read .project-os-agent.yml. Install with: pip install pyyaml"
        )

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a YAML object")

    version = raw.get("version", 1)
    templates_dir = raw.get("templates_dir", "templates")
    dry_run = raw.get("dry_run", True)
    managed_files_raw = raw.get("managed_files")
    placeholders = raw.get("placeholders", {})
    webhook = _validate_webhook(_ensure_yaml_object(raw.get("webhook"), "webhook"))
    policy = _validate_policy(_ensure_yaml_object(raw.get("policy"), "policy"))
    phase3 = _validate_phase3(_ensure_yaml_object(raw.get("phase3"), "phase3"))
    phase4 = _validate_phase4(_ensure_yaml_object(raw.get("phase4"), "phase4"))
    phase5 = _validate_phase5(_ensure_yaml_object(raw.get("phase5"), "phase5"))
    gitlab = _validate_gitlab(_ensure_yaml_object(raw.get("gitlab"), "gitlab"))

    if not isinstance(version, int) or version < 1:
        raise ValueError("'version' must be an integer >= 1")
    if not isinstance(templates_dir, str) or not templates_dir.strip():
        raise ValueError("'templates_dir' must be a non-empty string")
    if not isinstance(dry_run, bool):
        raise ValueError("'dry_run' must be a boolean")
    if not isinstance(placeholders, dict):
        raise ValueError("'placeholders' must be an object of string:string")

    for key, value in placeholders.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("'placeholders' must only contain string keys and values")

    repo_root = config_path.parent.resolve()
    template_root = (repo_root / templates_dir).resolve()
    if not _is_within(repo_root, template_root):
        raise ValueError("'templates_dir' must remain inside the repository root")

    managed_files: list[ManagedFile] = []
    if managed_files_raw is None:
        if not template_root.exists():
            raise FileNotFoundError(f"Templates directory not found: {template_root}")
        for template_path in sorted(template_root.glob("*.md")):
            managed_files.append(ManagedFile(template=template_path.name, target=template_path.name))
    else:
        if not isinstance(managed_files_raw, list):
            raise ValueError("'managed_files' must be an array")
        for index, item in enumerate(managed_files_raw):
            if not isinstance(item, dict):
                raise ValueError(f"managed_files[{index}] must be an object")
            managed_files.append(_validate_mapping(item, index))

    if not managed_files:
        raise ValueError("No managed files configured")

    for index, mapping in enumerate(managed_files):
        template_path = (template_root / mapping.template).resolve()
        target_path = (repo_root / mapping.target).resolve()
        if not _is_within(template_root, template_path):
            raise ValueError(
                f"managed_files[{index}].template points outside templates_dir: {mapping.template}"
            )
        if not _is_within(repo_root, target_path):
            raise ValueError(
                f"managed_files[{index}].target points outside repository: {mapping.target}"
            )

    return AgentConfig(
        version=version,
        templates_dir=templates_dir,
        dry_run=dry_run,
        managed_files=managed_files,
        webhook=webhook,
        policy=policy,
        phase3=phase3,
        phase4=phase4,
        phase5=phase5,
        gitlab=gitlab,
        placeholders=placeholders,
    )

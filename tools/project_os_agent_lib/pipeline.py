from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

from .audit import log_audit_event
from .cli_validation import resolve_webhook_settings
from .config import (
    CRITICAL_DOCS,
    ActionResult,
    AgentConfig,
    BlockedAction,
    EventPlan,
    Gap,
    LLMConfig,
    NormalizedEvent,
    Phase3Config,
    PolicyConfig,
    ProposedAction,
    _is_within,
    load_config,
    safe_text,
)
from .guardrails import (
    GuardrailViolation,
    assert_content_is_safe,
    assert_write_targets_allowed,
    enforce_rate_limit,
    payload_size_from_json,
    safe_write_enabled,
    validate_payload_size,
)


def _apply_placeholders(content: str, placeholders: dict[str, str]) -> str:
    rendered = content
    for token, value in placeholders.items():
        rendered = rendered.replace(token, value)
    return rendered


def _build_creation_diff(target: str, content: str) -> str:
    new_lines = content.splitlines()
    return "\n".join(
        difflib.unified_diff([], new_lines, fromfile="/dev/null", tofile=target, lineterm="")
    )


def create_missing_files(repo_root: Path, config: AgentConfig, dry_run: bool) -> list[ActionResult]:
    template_root = (repo_root / config.templates_dir).resolve()
    results: list[ActionResult] = []

    for mapping in config.managed_files:
        template_path = (template_root / mapping.template).resolve()
        target_path = (repo_root / mapping.target).resolve()

        if not template_path.is_file():
            raise FileNotFoundError(f"Template file not found: {template_path}")

        if target_path.exists():
            results.append(ActionResult(target=mapping.target, status="skipped_existing"))
            continue

        rendered = _apply_placeholders(
            template_path.read_text(encoding="utf-8"), config.placeholders
        )
        diff = _build_creation_diff(mapping.target, rendered)
        try:
            assert_write_targets_allowed(repo_root, config, [mapping.target])
            assert_content_is_safe(config, mapping.target, rendered)
        except GuardrailViolation:
            if safe_write_enabled(config):
                results.append(ActionResult(target=mapping.target, status="blocked_guardrail"))
                continue
            raise

        if dry_run:
            results.append(ActionResult(target=mapping.target, status="would_create", diff=diff))
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        results.append(ActionResult(target=mapping.target, status="created", diff=diff))

    return results


def _print_results(results: list[ActionResult], dry_run: bool, show_diff: bool) -> None:
    mode_label = "DRY-RUN" if dry_run else "APPLY"
    print(f"[project-os-agent] Mode: {mode_label}")

    for result in results:
        print(f"- {result.status}: {result.target}")
        if show_diff and result.diff and result.status in {"would_create", "created", "would_update", "updated"}:
            print(result.diff)
            print()

    created_like = sum(1 for r in results if r.status in {"would_create", "created"})
    updated_like = sum(1 for r in results if r.status in {"would_update", "updated"})
    skipped = sum(
        1
        for r in results
        if r.status
        in {
            "skipped_existing",
            "unchanged",
            "adr_already_recorded",
            "blocked_guardrail",
        }
    )
    print(
        f"[project-os-agent] Summary: {created_like} file(s) "
        f"{'to create' if dry_run else 'created'}, "
        f"{updated_like} file(s) {'to update' if dry_run else 'updated'}, "
        f"{skipped} file(s) unchanged."
    )


def _build_update_diff(target: str, original: str, updated: str) -> str:
    before_lines = original.splitlines()
    after_lines = updated.splitlines()
    return "\n".join(
        difflib.unified_diff(before_lines, after_lines, fromfile=target, tofile=target, lineterm="")
    )


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _gemini_generate_json(prompt: str, llm_config: LLMConfig) -> dict[str, Any] | None:
    if not llm_config.enabled:
        return None

    api_key = os.getenv(llm_config.api_key_env, "").strip()
    if not api_key:
        return None

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{llm_config.model}:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    request = urlrequest.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlrequest.urlopen(request, timeout=llm_config.timeout_seconds) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError):
        return None

    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        return None
    content = first_candidate.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        return None
    first_part = parts[0]
    if not isinstance(first_part, dict):
        return None
    text = safe_text(first_part.get("text"))
    return _extract_json_from_text(text)


def _load_mcp_context(repo_root: Path, config: Phase3Config) -> dict[str, Any]:
    if not config.mcp.enabled:
        return {}

    context_path = (repo_root / config.mcp.context_file).resolve()
    if not _is_within(repo_root, context_path):
        raise ValueError("phase3.mcp.context_file points outside repository")
    if not context_path.exists():
        return {}

    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in MCP context file {context_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"MCP context file must contain a JSON object: {context_path}")
    return payload


def _detect_stale_docs(
    repo_root: Path, docs_presence: dict[str, bool], stale_days: int
) -> list[dict[str, Any]]:
    stale_docs: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for target, present in docs_presence.items():
        if not present:
            continue
        target_path = repo_root / target
        modified_at = datetime.fromtimestamp(target_path.stat().st_mtime, tz=timezone.utc)
        age_days = (now - modified_at).days
        if age_days >= stale_days:
            stale_docs.append(
                {
                    "file": target,
                    "age_days": age_days,
                    "last_modified": modified_at.date().isoformat(),
                }
            )
    stale_docs.sort(key=lambda item: int(item.get("age_days", 0)), reverse=True)
    return stale_docs


def _markdown_find_section(lines: list[str], heading: str) -> tuple[int, int, int, int] | None:
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip().lower()
        if title != heading.strip().lower():
            continue

        start = index + 1
        end = len(lines)
        for cursor in range(start, len(lines)):
            next_match = heading_pattern.match(lines[cursor])
            if next_match and len(next_match.group(1)) <= level:
                end = cursor
                break
        return index, start, end, level
    return None


def _markdown_update_section(
    text: str,
    heading: str,
    body_lines: list[str],
    *,
    heading_level: int = 2,
) -> tuple[str, bool]:
    lines = text.splitlines()
    section = _markdown_find_section(lines, heading)
    normalized_body = body_lines[:]

    if section is None:
        updated_lines = lines[:]
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_lines.append(f"{'#' * heading_level} {heading}")
        updated_lines.append("")
        updated_lines.extend(normalized_body)
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_text = "\n".join(updated_lines).rstrip() + "\n"
        return updated_text, updated_text != (text.rstrip() + ("\n" if text else ""))

    _, start, end, _ = section
    replacement = ["", *normalized_body]
    if replacement[-1] != "":
        replacement.append("")
    updated_lines = lines[:start] + replacement + lines[end:]
    updated_text = "\n".join(updated_lines).rstrip() + "\n"
    original = text.rstrip() + ("\n" if text else "")
    return updated_text, updated_text != original


def _ensure_targets_exist(
    repo_root: Path, config: AgentConfig, targets: set[str], dry_run: bool
) -> tuple[list[ActionResult], set[str]]:
    results: list[ActionResult] = []
    created_targets: set[str] = set()
    template_root = (repo_root / config.templates_dir).resolve()

    for mapping in config.managed_files:
        if mapping.target not in targets:
            continue
        target_path = (repo_root / mapping.target).resolve()
        if target_path.exists():
            continue

        template_path = (template_root / mapping.template).resolve()
        if not template_path.is_file():
            raise FileNotFoundError(f"Template file not found: {template_path}")
        content = _apply_placeholders(template_path.read_text(encoding="utf-8"), config.placeholders)
        diff = _build_creation_diff(mapping.target, content)
        try:
            assert_write_targets_allowed(repo_root, config, [mapping.target])
            assert_content_is_safe(config, mapping.target, content)
        except GuardrailViolation:
            if safe_write_enabled(config):
                results.append(ActionResult(target=mapping.target, status="blocked_guardrail"))
                continue
            raise

        if dry_run:
            results.append(ActionResult(target=mapping.target, status="would_create", diff=diff))
            created_targets.add(mapping.target)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        results.append(ActionResult(target=mapping.target, status="created", diff=diff))
        created_targets.add(mapping.target)

    return results, created_targets


def _render_target_template(repo_root: Path, config: AgentConfig, target: str) -> str:
    template_root = (repo_root / config.templates_dir).resolve()
    for mapping in config.managed_files:
        if mapping.target != target:
            continue
        template_path = (template_root / mapping.template).resolve()
        if not template_path.is_file():
            raise FileNotFoundError(f"Template file not found: {template_path}")
        return _apply_placeholders(template_path.read_text(encoding="utf-8"), config.placeholders)
    raise ValueError(f"No template mapping configured for target: {target}")


def _format_next_steps_markdown(next_steps: list[dict[str, str]]) -> list[str]:
    if not next_steps:
        return [
            "- [ ] Step: Aucun next step prioritaire detecte",
            "      Owner: TBD",
            "      Depends on: none",
            "      Priority: P2",
            "      Status: Pending",
            "      Evidence: none",
        ]

    lines: list[str] = []
    for step in next_steps:
        lines.append(f"- [ ] Step: {step['description']}")
        lines.append(f"      Owner: {step['owner']}")
        lines.append(f"      Depends on: {step['depends_on']}")
        lines.append(f"      Priority: {step['priority']}")
        lines.append(f"      Status: {step['status']}")
        lines.append(f"      Evidence: {step['evidence']}")
    return lines


def _next_adr_number(decisions_content: str) -> int:
    matches = re.findall(r"^##\s+ADR-(\d+)\b", decisions_content, flags=re.MULTILINE)
    if not matches:
        return 1
    return max(int(value) for value in matches) + 1


def _build_adr_entry(adr_number: int, adr_draft: dict[str, Any]) -> str:
    decision = safe_text(adr_draft.get("decision"))
    rationale = adr_draft.get("rationale")
    impact = adr_draft.get("impact")
    reference = safe_text(adr_draft.get("reference"))
    date_value = safe_text(adr_draft.get("date"))

    rationale_lines = rationale if isinstance(rationale, list) else [str(rationale or "")]
    impact_lines = impact if isinstance(impact, list) else [str(impact or "")]

    lines = [
        f"## ADR-{adr_number:03d}",
        "",
        f"Date: {date_value}",
        "",
        "Decision:",
        decision,
        "",
        "Rationale:",
    ]
    for line in rationale_lines:
        if line:
            lines.append(f"- {line}")
    if lines[-1] != "":
        lines.append("")
    lines.append("Impact:")
    for line in impact_lines:
        if line:
            lines.append(f"- {line}")
    if reference:
        lines.extend(["", f"Reference: {reference}"])
    lines.append("")
    return "\n".join(lines)


def _gemini_refine_next_steps(
    event: NormalizedEvent,
    gaps: list[Gap],
    next_steps: list[dict[str, str]],
    llm_config: LLMConfig,
) -> list[dict[str, str]]:
    if not llm_config.enabled:
        return next_steps

    prompt_payload = {
        "event": {
            "event_type": event.event_type,
            "action": event.action,
            "state": event.state,
            "title": event.title,
            "reference": event.reference,
        },
        "gaps": [
            {"code": gap.code, "severity": gap.severity, "summary": gap.summary}
            for gap in gaps
        ],
        "next_steps": next_steps,
    }
    prompt = (
        "Refine this project governance action plan.\n"
        "Return ONLY JSON object with key next_steps.\n"
        "Each item must keep keys: description, owner, depends_on, priority, status, evidence.\n"
        "Allowed priority: P0,P1,P2. Allowed status: Pending,Blocked,In Progress.\n"
        f"Input:\n{json.dumps(prompt_payload, ensure_ascii=True)}"
    )
    response = _gemini_generate_json(prompt, llm_config)
    if not response:
        return next_steps

    candidate_steps = response.get("next_steps")
    if not isinstance(candidate_steps, list):
        return next_steps

    normalized_steps: list[dict[str, str]] = []
    for item in candidate_steps:
        if not isinstance(item, dict):
            continue
        description = safe_text(item.get("description")).strip()
        owner = safe_text(item.get("owner")).strip()
        depends_on = safe_text(item.get("depends_on")).strip()
        priority = safe_text(item.get("priority")).strip()
        status = safe_text(item.get("status")).strip()
        evidence = safe_text(item.get("evidence")).strip()

        if not description:
            continue
        if priority not in {"P0", "P1", "P2"}:
            priority = "P1"
        if status not in {"Pending", "Blocked", "In Progress"}:
            status = "Pending"
        normalized_steps.append(
            {
                "description": description,
                "owner": owner or "TBD",
                "depends_on": depends_on or "none",
                "priority": priority,
                "status": status,
                "evidence": evidence or "none",
            }
        )

    return normalized_steps or next_steps


def _generate_next_steps(
    event: NormalizedEvent,
    gaps: list[Gap],
    allowed_actions: list[ProposedAction],
    phase3: Phase3Config,
    mcp_context: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    severity_by_gap = {gap.code: gap.severity for gap in gaps}
    owners_by_action = mcp_context.get("owners_by_action", {})
    priority_overrides = mcp_context.get("priority_overrides", {})
    depends_on_map = mcp_context.get("depends_on", {})

    next_steps: list[dict[str, str]] = []
    for action in allowed_actions:
        related_priority = "P2"
        if action.action_type in {"open_merge_request", "create_issue", "write_adr"}:
            related_priority = "P1"
        if severity_by_gap.get("pipeline_failure") == "high" and action.action_type in {
            "create_issue",
            "update_documentation",
        }:
            related_priority = "P0"
        if severity_by_gap.get("docs_missing") == "high" and action.action_type == "open_merge_request":
            related_priority = "P0"

        override_value = None
        if isinstance(priority_overrides, dict):
            override_value = priority_overrides.get(action.action_type)
            if override_value not in {"P0", "P1", "P2"}:
                override_value = None

        owner_value = phase3.next_steps_default_owner
        if isinstance(owners_by_action, dict):
            owner_candidate = owners_by_action.get(action.action_type)
            if isinstance(owner_candidate, str) and owner_candidate.strip():
                owner_value = owner_candidate.strip()

        depends_on = "none"
        if isinstance(depends_on_map, dict):
            candidate_depends = depends_on_map.get(action.action_type)
            if isinstance(candidate_depends, str) and candidate_depends.strip():
                depends_on = candidate_depends.strip()

        next_steps.append(
            {
                "description": action.summary,
                "owner": owner_value,
                "depends_on": depends_on,
                "priority": override_value or related_priority,
                "status": "Pending",
                "evidence": action.target or event.reference or "none",
            }
        )

    if not next_steps:
        return [], "heuristic"

    refined_steps = _gemini_refine_next_steps(event, gaps, next_steps, phase3.llm)
    source = "gemini" if refined_steps != next_steps and phase3.llm.enabled else "heuristic"
    return refined_steps, source


def _build_adr_draft(
    event: NormalizedEvent,
    gaps: list[Gap],
    allowed_actions: list[ProposedAction],
) -> dict[str, Any] | None:
    if not any(action.action_type == "write_adr" for action in allowed_actions):
        return None

    rationale = [gap.summary for gap in gaps[:3]]
    if not rationale:
        rationale = ["Document governance decision for traceability"]

    impact = [action.summary for action in allowed_actions[:3]]
    if not impact:
        impact = ["No direct implementation impact yet"]

    decision = "Record governance decision generated by Project OS Agent"
    if event.event_type == "pipeline":
        decision = "Treat failed pipelines as project risk requiring immediate remediation"
    elif event.event_type == "merge_request":
        decision = f"Track technical decision linked to merged MR {event.reference}"
    elif event.event_type == "issue":
        decision = f"Track delivery decision linked to issue {event.reference}"

    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "decision": decision,
        "rationale": rationale,
        "impact": impact,
        "reference": event.reference or event.url,
    }


def _infer_event_name_from_payload(payload: dict[str, Any]) -> str:
    object_kind = safe_text(payload.get("object_kind")).lower()
    if object_kind == "merge_request":
        return "Merge Request Hook"
    if object_kind == "issue":
        return "Issue Hook"
    if object_kind == "pipeline":
        return "Pipeline Hook"
    return ""


def _event_type_from_name(event_name: str, payload: dict[str, Any]) -> str:
    lower_name = event_name.lower()
    if "merge request" in lower_name:
        return "merge_request"
    if "issue" in lower_name:
        return "issue"
    if "pipeline" in lower_name:
        return "pipeline"

    object_kind = safe_text(payload.get("object_kind")).lower()
    if object_kind in {"merge_request", "issue", "pipeline"}:
        return object_kind
    return "unknown"


def normalize_gitlab_event(event_name: str, payload: dict[str, Any]) -> NormalizedEvent:
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object")

    resolved_event_name = event_name.strip() or _infer_event_name_from_payload(payload)
    event_type = _event_type_from_name(resolved_event_name, payload)
    object_attributes = payload.get("object_attributes")
    if not isinstance(object_attributes, dict):
        object_attributes = {}

    project_raw = payload.get("project")
    project_data = project_raw if isinstance(project_raw, dict) else {}
    project = safe_text(
        project_data.get("path_with_namespace")
        or project_data.get("web_url")
        or payload.get("project_id")
    )
    user_raw = payload.get("user")
    user_data = user_raw if isinstance(user_raw, dict) else {}
    author = safe_text(user_data.get("username") or user_data.get("name") or payload.get("user_name"))

    action = "unknown"
    state = "unknown"
    title = ""
    url = ""
    reference = ""

    if event_type == "merge_request":
        action = safe_text(object_attributes.get("action") or payload.get("action") or "unknown")
        state = safe_text(
            object_attributes.get("state") or ("merged" if action in {"merge", "merged"} else "unknown")
        )
        title = safe_text(object_attributes.get("title") or payload.get("title"))
        url = safe_text(object_attributes.get("url") or object_attributes.get("target"))
        iid = object_attributes.get("iid")
        reference = f"!{iid}" if isinstance(iid, int) else "merge_request"
    elif event_type == "issue":
        action = safe_text(object_attributes.get("action") or payload.get("action") or "unknown")
        state = safe_text(object_attributes.get("state") or "opened")
        title = safe_text(object_attributes.get("title") or payload.get("title"))
        url = safe_text(object_attributes.get("url"))
        iid = object_attributes.get("iid")
        reference = f"#{iid}" if isinstance(iid, int) else "issue"
    elif event_type == "pipeline":
        status = safe_text(object_attributes.get("status") or payload.get("status") or "unknown")
        action = status
        state = status
        pipeline_id = object_attributes.get("id")
        title = f"Pipeline {pipeline_id} ({status})" if pipeline_id else f"Pipeline ({status})"
        url = safe_text(object_attributes.get("url"))
        reference = f"pipeline:{pipeline_id}" if isinstance(pipeline_id, int) else "pipeline"
    else:
        action = safe_text(payload.get("action") or "unknown")
        state = safe_text(payload.get("state") or "unknown")
        title = safe_text(payload.get("title") or payload.get("object_kind") or "Unsupported event")
        url = safe_text(payload.get("url"))
        reference = "event"

    return NormalizedEvent(
        event_name=resolved_event_name or "Unknown",
        event_type=event_type,
        action=action,
        state=state,
        title=title,
        author=author,
        url=url,
        project=project,
        reference=reference,
        payload=payload,
    )


def extract_context(repo_root: Path, config: AgentConfig, event: NormalizedEvent) -> dict[str, Any]:
    docs_presence: dict[str, bool] = {}
    for mapping in config.managed_files:
        docs_presence[mapping.target] = (repo_root / mapping.target).exists()

    missing_critical_docs = sorted(doc for doc in CRITICAL_DOCS if not docs_presence.get(doc, False))
    stale_docs = _detect_stale_docs(repo_root, docs_presence, config.phase3.stale_days)
    mcp_context = _load_mcp_context(repo_root, config.phase3)

    project_status_path = repo_root / "PROJECT_STATUS.md"
    status_mentions_at_risk = False
    if project_status_path.exists():
        status_content = project_status_path.read_text(encoding="utf-8").lower()
        status_mentions_at_risk = "at risk" in status_content

    issue_description = ""
    if event.event_type == "issue":
        object_attributes = event.payload.get("object_attributes")
        if isinstance(object_attributes, dict):
            issue_description = safe_text(object_attributes.get("description"))

    return {
        "docs_presence": docs_presence,
        "missing_critical_docs": missing_critical_docs,
        "stale_docs": stale_docs,
        "project_status_mentions_at_risk": status_mentions_at_risk,
        "issue_description": issue_description,
        "mcp_context": mcp_context,
    }


def detect_gaps(event: NormalizedEvent, context: dict[str, Any]) -> list[Gap]:
    gaps: list[Gap] = []
    missing_docs: list[str] = context.get("missing_critical_docs", [])
    stale_docs: list[dict[str, Any]] = context.get("stale_docs", [])
    if missing_docs:
        gaps.append(
            Gap(
                code="docs_missing",
                severity="high",
                summary="Critical governance docs are missing in repository root",
                evidence=missing_docs,
            )
        )

    if stale_docs:
        stale_files = [item.get("file", "") for item in stale_docs[:5] if item.get("file")]
        gaps.append(
            Gap(
                code="docs_stale",
                severity="medium",
                summary="Some documentation files are stale and should be refreshed",
                evidence=stale_files or ["documentation files"],
            )
        )

    if event.event_type == "merge_request" and event.action in {"merge", "merged"}:
        gaps.append(
            Gap(
                code="post_merge_sync_required",
                severity="medium",
                summary="Merged MR requires documentation sync",
                evidence=[event.reference or event.url],
            )
        )
        if re.search(r"(arch|api|auth|security|infra|schema|migration)", event.title, flags=re.IGNORECASE):
            gaps.append(
                Gap(
                    code="decision_record_missing",
                    severity="medium",
                    summary="Merged MR likely contains technical decisions that should be logged in DECISIONS.md",
                    evidence=[event.reference or event.url, event.title],
                )
            )

    if event.event_type == "issue":
        description = safe_text(context.get("issue_description", ""))
        if len(description.strip()) < 60:
            gaps.append(
                Gap(
                    code="issue_context_missing",
                    severity="medium",
                    summary="Issue description is too short to be actionable",
                    evidence=[event.reference or event.url],
                )
            )

        lowered = description.lower()
        if "acceptance criteria" not in lowered and "- [ ]" not in description:
            gaps.append(
                Gap(
                    code="issue_acceptance_missing",
                    severity="medium",
                    summary="Issue does not include acceptance criteria",
                    evidence=[event.reference or event.url],
                )
            )

    if event.event_type == "pipeline" and event.state.lower() in {"failed", "canceled"}:
        gaps.append(
            Gap(
                code="pipeline_failure",
                severity="high",
                summary="Pipeline is failing and requires remediation",
                evidence=[event.reference or event.url],
            )
        )
        if not context.get("project_status_mentions_at_risk", False):
            gaps.append(
                Gap(
                    code="risk_not_reflected",
                    severity="medium",
                    summary='PROJECT_STATUS.md does not yet mention "At Risk"',
                    evidence=["PROJECT_STATUS.md"],
                )
            )

    if event.event_type == "unknown":
        gaps.append(
            Gap(
                code="unsupported_event",
                severity="low",
                summary="Received unsupported event type",
                evidence=[event.event_name],
            )
        )

    return gaps


def _action(
    action_type: str,
    summary: str,
    *,
    target: str = "",
    details: dict[str, Any] | None = None,
) -> ProposedAction:
    return ProposedAction(
        action_type=action_type,
        summary=summary,
        target=target,
        details=details or {},
    )


def propose_actions(
    event: NormalizedEvent, context: dict[str, Any], gaps: list[Gap]
) -> list[ProposedAction]:
    actions: list[ProposedAction] = []
    gap_codes = {gap.code for gap in gaps}
    missing_docs: list[str] = context.get("missing_critical_docs", [])

    if missing_docs:
        actions.append(
            _action(
                "open_merge_request",
                "Bootstrap missing governance docs from templates",
                target="docs/bootstrap",
                details={"missing_files": missing_docs},
            )
        )

    if "docs_stale" in gap_codes:
        actions.append(
            _action(
                "update_documentation",
                "Refresh stale governance documentation",
                target="PROJECT_STATUS.md",
                details={"source": "staleness_detector"},
            )
        )

    if event.event_type == "merge_request" and event.action in {"merge", "merged"}:
        actions.append(
            _action(
                "update_documentation",
                "Sync project status after merged MR",
                target="PROJECT_STATUS.md",
                details={"trigger": event.reference},
            )
        )
        if "decision_record_missing" in gap_codes:
            actions.append(
                _action(
                    "write_adr",
                    "Record merged MR technical decision in DECISIONS.md",
                    target="DECISIONS.md",
                    details={"trigger": event.reference, "title": event.title},
                )
            )

    if event.event_type == "issue":
        actions.append(
            _action(
                "update_documentation",
                "Sync active tasks and blockers with issue update",
                target="PROJECT_STATUS.md",
                details={"trigger": event.reference},
            )
        )
        if {"issue_context_missing", "issue_acceptance_missing"} & gap_codes:
            actions.append(
                _action(
                    "comment_issue",
                    "Request missing context and acceptance criteria",
                    target=event.reference,
                    details={
                        "checklist": [
                            "Business context",
                            "Expected result",
                            "Acceptance criteria",
                        ]
                    },
                )
            )

    if event.event_type == "pipeline" and event.state.lower() in {"failed", "canceled"}:
        actions.append(
            _action(
                "create_issue",
                "Open remediation issue for failed pipeline",
                target=event.reference,
                details={"pipeline_status": event.state},
            )
        )
        actions.append(
            _action(
                "update_documentation",
                'Mark project as "At Risk" after pipeline failure',
                target="PROJECT_STATUS.md",
                details={"trigger": event.reference},
            )
        )

    deduped: list[ProposedAction] = []
    seen: set[tuple[str, str, str]] = set()
    for action in actions:
        key = (action.action_type, action.target, action.summary)
        if key not in seen:
            seen.add(key)
            deduped.append(action)
    return deduped


def apply_policy(
    actions: list[ProposedAction], policy: PolicyConfig
) -> tuple[list[ProposedAction], list[BlockedAction]]:
    allowed: list[ProposedAction] = []
    blocked: list[BlockedAction] = []

    for action in actions:
        if action.action_type in policy.forbidden_actions:
            blocked.append(
                BlockedAction(
                    action=action,
                    reason=f"Action type is forbidden by policy: {action.action_type}",
                )
            )
            continue
        if policy.allowed_actions and action.action_type not in policy.allowed_actions:
            blocked.append(
                BlockedAction(
                    action=action,
                    reason=f"Action type is not allowed by policy: {action.action_type}",
                )
            )
            continue
        allowed.append(action)

    return allowed, blocked


def process_event_pipeline(
    repo_root: Path, config: AgentConfig, event_name: str, payload: dict[str, Any]
) -> EventPlan:
    normalized_event = normalize_gitlab_event(event_name, payload)
    context = extract_context(repo_root, config, normalized_event)
    gaps = detect_gaps(normalized_event, context)
    proposed_actions = propose_actions(normalized_event, context, gaps)
    allowed_actions, blocked_actions = apply_policy(proposed_actions, config.policy)
    mcp_context = context.get("mcp_context")
    if not isinstance(mcp_context, dict):
        mcp_context = {}
    next_steps, next_steps_source = _generate_next_steps(
        normalized_event,
        gaps,
        allowed_actions,
        config.phase3,
        mcp_context,
    )
    adr_draft = _build_adr_draft(normalized_event, gaps, allowed_actions)
    return EventPlan(
        normalized_event=normalized_event,
        context=context,
        gaps=gaps,
        proposed_actions=proposed_actions,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        next_steps=next_steps,
        next_steps_source=next_steps_source,
        adr_draft=adr_draft,
    )


def _event_plan_to_dict(plan: EventPlan) -> dict[str, Any]:
    def action_to_dict(action: ProposedAction) -> dict[str, Any]:
        return {
            "type": action.action_type,
            "summary": action.summary,
            "target": action.target,
            "details": action.details,
        }

    return {
        "normalized_event": {
            "event_name": plan.normalized_event.event_name,
            "event_type": plan.normalized_event.event_type,
            "action": plan.normalized_event.action,
            "state": plan.normalized_event.state,
            "title": plan.normalized_event.title,
            "author": plan.normalized_event.author,
            "url": plan.normalized_event.url,
            "project": plan.normalized_event.project,
            "reference": plan.normalized_event.reference,
        },
        "context": plan.context,
        "gaps": [
            {
                "code": gap.code,
                "severity": gap.severity,
                "summary": gap.summary,
                "evidence": gap.evidence,
            }
            for gap in plan.gaps
        ],
        "proposed_actions": [action_to_dict(action) for action in plan.proposed_actions],
        "allowed_actions": [action_to_dict(action) for action in plan.allowed_actions],
        "blocked_actions": [
            {
                "action": action_to_dict(blocked.action),
                "reason": blocked.reason,
            }
            for blocked in plan.blocked_actions
        ],
        "next_steps": plan.next_steps,
        "next_steps_source": plan.next_steps_source,
        "adr_draft": plan.adr_draft,
    }


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        payload_data = json.loads(args.payload_json)
    elif args.payload_file:
        payload_data = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    else:
        payload_data = json.loads(sys.stdin.read())

    if not isinstance(payload_data, dict):
        raise ValueError("Event payload must decode to a JSON object")
    return payload_data


def run_bootstrap(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()
    started = time.perf_counter()
    status = "success"
    dry_run = args.dry_run or (config.dry_run and not args.apply)
    results: list[ActionResult] = []

    try:
        results = create_missing_files(repo_root, config, dry_run=dry_run)
        _print_results(results, dry_run=dry_run, show_diff=not args.no_diff)
        return 0
    except GuardrailViolation:
        status = "rejected_guardrail"
        raise
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_audit_event(
            repo_root,
            config,
            "command",
            status,
            command="bootstrap",
            duration_ms=duration_ms,
            details={
                "dry_run": dry_run,
                "created_count": sum(1 for item in results if item.status == "created"),
                "updated_count": sum(1 for item in results if item.status == "updated"),
                "blocked_guardrail_count": sum(
                    1 for item in results if item.status == "blocked_guardrail"
                ),
            },
        )


def run_process_event(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()
    started = time.perf_counter()
    status = "success"
    event_type = ""
    event_reference = ""
    event_state = ""
    blocked_actions_count = 0

    try:
        payload = _read_payload(args)
        validate_payload_size(config, payload_size_from_json(payload), "process-event")
        enforce_rate_limit(repo_root, config, "process-event")
        event_name = args.event_name or _infer_event_name_from_payload(payload)
        plan = process_event_pipeline(repo_root, config, event_name, payload)
        event_type = plan.normalized_event.event_type
        event_reference = plan.normalized_event.reference
        event_state = plan.normalized_event.state
        blocked_actions_count = len(plan.blocked_actions)
        print(json.dumps(_event_plan_to_dict(plan), indent=2, ensure_ascii=True))
        return 0
    except GuardrailViolation:
        status = "rejected_guardrail"
        raise
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_audit_event(
            repo_root,
            config,
            "command",
            status,
            command="process-event",
            event_type=event_type,
            reference=event_reference,
            duration_ms=duration_ms,
            details={
                "event_state": event_state,
                "blocked_actions_count": blocked_actions_count,
            },
        )


def sync_docs_from_plan(
    repo_root: Path,
    config: AgentConfig,
    plan: EventPlan,
    dry_run: bool,
    show_diff: bool,
) -> list[ActionResult]:
    results: list[ActionResult] = []
    ensure_results, created_targets = _ensure_targets_exist(
        repo_root,
        config,
        {"PROJECT_STATUS.md", "DECISIONS.md"},
        dry_run=dry_run,
    )
    results.extend(ensure_results)

    project_status_path = repo_root / "PROJECT_STATUS.md"
    if project_status_path.exists():
        status_original = project_status_path.read_text(encoding="utf-8")
    elif dry_run and "PROJECT_STATUS.md" in created_targets:
        status_original = _render_target_template(repo_root, config, "PROJECT_STATUS.md")
    else:
        status_original = ""

    next_steps_lines = _format_next_steps_markdown(plan.next_steps)
    status_updated, status_changed = _markdown_update_section(
        status_original,
        "Next Steps (Agent)",
        next_steps_lines,
        heading_level=2,
    )
    if status_changed:
        diff = _build_update_diff("PROJECT_STATUS.md", status_original, status_updated)
        if dry_run:
            results.append(ActionResult(target="PROJECT_STATUS.md", status="would_update", diff=diff))
        else:
            try:
                assert_write_targets_allowed(repo_root, config, ["PROJECT_STATUS.md"])
                assert_content_is_safe(config, "PROJECT_STATUS.md", status_updated)
            except GuardrailViolation:
                if safe_write_enabled(config):
                    results.append(ActionResult(target="PROJECT_STATUS.md", status="blocked_guardrail"))
                else:
                    raise
            else:
                project_status_path.write_text(status_updated, encoding="utf-8")
                results.append(ActionResult(target="PROJECT_STATUS.md", status="updated", diff=diff))
    else:
        results.append(ActionResult(target="PROJECT_STATUS.md", status="unchanged"))

    decisions_path = repo_root / "DECISIONS.md"
    if decisions_path.exists():
        decisions_original = decisions_path.read_text(encoding="utf-8")
    elif dry_run and "DECISIONS.md" in created_targets:
        decisions_original = _render_target_template(repo_root, config, "DECISIONS.md")
    else:
        decisions_original = ""
    adr_draft = plan.adr_draft
    if adr_draft:
        reference = safe_text(adr_draft.get("reference")).strip()
        if reference and reference in decisions_original:
            results.append(ActionResult(target="DECISIONS.md", status="adr_already_recorded"))
        else:
            adr_number = _next_adr_number(decisions_original)
            entry = _build_adr_entry(adr_number, adr_draft)
            decisions_updated = decisions_original.rstrip() + ("\n\n" if decisions_original.strip() else "")
            decisions_updated += entry
            decisions_updated = decisions_updated.rstrip() + "\n"
            diff = _build_update_diff("DECISIONS.md", decisions_original, decisions_updated)
            if dry_run:
                results.append(ActionResult(target="DECISIONS.md", status="would_update", diff=diff))
            else:
                try:
                    assert_write_targets_allowed(repo_root, config, ["DECISIONS.md"])
                    assert_content_is_safe(config, "DECISIONS.md", decisions_updated)
                except GuardrailViolation:
                    if safe_write_enabled(config):
                        results.append(ActionResult(target="DECISIONS.md", status="blocked_guardrail"))
                    else:
                        raise
                else:
                    decisions_path.write_text(decisions_updated, encoding="utf-8")
                    results.append(ActionResult(target="DECISIONS.md", status="updated", diff=diff))
    else:
        results.append(ActionResult(target="DECISIONS.md", status="unchanged"))

    _print_results(results, dry_run=dry_run, show_diff=show_diff)
    return results


def run_sync_docs(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()
    started = time.perf_counter()
    status = "success"
    dry_run = args.dry_run or (config.dry_run and not args.apply)
    event_type = ""
    event_reference = ""
    event_state = ""
    results: list[ActionResult] = []

    try:
        payload = _read_payload(args)
        validate_payload_size(config, payload_size_from_json(payload), "sync-docs")
        enforce_rate_limit(repo_root, config, "sync-docs")
        event_name = args.event_name or _infer_event_name_from_payload(payload)
        plan = process_event_pipeline(repo_root, config, event_name, payload)
        event_type = plan.normalized_event.event_type
        event_reference = plan.normalized_event.reference
        event_state = plan.normalized_event.state

        results = sync_docs_from_plan(
            repo_root,
            config,
            plan,
            dry_run=dry_run,
            show_diff=not args.no_diff,
        )
        print("[project-os-agent] Sync summary:")
        print(
            json.dumps(
                {
                    "event_type": plan.normalized_event.event_type,
                    "next_steps_source": plan.next_steps_source,
                    "next_steps_count": len(plan.next_steps),
                    "adr_generated": bool(plan.adr_draft),
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return 0
    except GuardrailViolation:
        status = "rejected_guardrail"
        raise
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_audit_event(
            repo_root,
            config,
            "command",
            status,
            command="sync-docs",
            event_type=event_type,
            reference=event_reference,
            duration_ms=duration_ms,
            details={
                "dry_run": dry_run,
                "event_state": event_state,
                "created_count": sum(1 for item in results if item.status == "created"),
                "updated_count": sum(1 for item in results if item.status == "updated"),
                "blocked_guardrail_count": sum(
                    1 for item in results if item.status == "blocked_guardrail"
                ),
            },
        )


def _send_json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_webhook_handler(
    repo_root: Path,
    config: AgentConfig,
    webhook_path: str,
    expected_token: str | None,
) -> type[BaseHTTPRequestHandler]:
    class GitLabWebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            started = time.perf_counter()
            audit_status = "success"
            audit_event_type = ""
            audit_reference = ""
            audit_state = ""

            if urlparse(self.path).path != webhook_path:
                _send_json_response(self, 404, {"status": "error", "error": "Not Found"})
                return

            if expected_token is not None:
                received_token = self.headers.get("X-Gitlab-Token")
                if received_token != expected_token:
                    _send_json_response(self, 403, {"status": "error", "error": "Forbidden"})
                    return

            event_name = safe_text(self.headers.get("X-Gitlab-Event"))
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                _send_json_response(
                    self,
                    400,
                    {"status": "error", "error": "Invalid Content-Length header"},
                )
                return

            try:
                validate_payload_size(config, content_length, "webhook")
            except GuardrailViolation as exc:
                audit_status = "rejected_guardrail"
                _send_json_response(
                    self,
                    413,
                    {"status": "error", "error": str(exc)},
                )
                log_audit_event(
                    repo_root,
                    config,
                    "command",
                    audit_status,
                    command="webhook",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    details={"event_name": event_name, "error": str(exc)},
                )
                return

            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"

            try:
                payload = json.loads(raw_body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Payload must be a JSON object")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                _send_json_response(
                    self,
                    400,
                    {"status": "error", "error": f"Invalid payload: {exc}"},
                )
                return

            try:
                enforce_rate_limit(repo_root, config, "webhook")
                plan = process_event_pipeline(repo_root, config, event_name, payload)
                audit_event_type = plan.normalized_event.event_type
                audit_reference = plan.normalized_event.reference
                audit_state = plan.normalized_event.state
            except GuardrailViolation as exc:
                audit_status = "rejected_guardrail"
                _send_json_response(
                    self,
                    429,
                    {"status": "error", "error": str(exc)},
                )
                log_audit_event(
                    repo_root,
                    config,
                    "command",
                    audit_status,
                    command="webhook",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    details={"event_name": event_name, "error": str(exc)},
                )
                return
            except Exception as exc:  # noqa: BLE001
                audit_status = "error"
                _send_json_response(
                    self,
                    500,
                    {"status": "error", "error": f"Event processing failed: {exc}"},
                )
                log_audit_event(
                    repo_root,
                    config,
                    "command",
                    audit_status,
                    command="webhook",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    details={"event_name": event_name, "error": str(exc)},
                )
                return

            _send_json_response(
                self,
                200,
                {
                    "status": "ok",
                    "plan": _event_plan_to_dict(plan),
                },
            )
            log_audit_event(
                repo_root,
                config,
                "command",
                audit_status,
                command="webhook",
                event_type=audit_event_type,
                reference=audit_reference,
                duration_ms=int((time.perf_counter() - started) * 1000),
                details={
                    "event_name": event_name,
                    "event_state": audit_state,
                    "blocked_actions_count": len(plan.blocked_actions),
                },
            )

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == webhook_path:
                _send_json_response(
                    self,
                    200,
                    {
                        "status": "ok",
                        "message": "Project OS Agent webhook is running",
                    },
                )
                return
            _send_json_response(self, 404, {"status": "error", "error": "Not Found"})

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write(f"[project-os-agent] webhook - {format % args}\n")

    return GitLabWebhookHandler


def run_serve_webhook(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()

    host, port, path = resolve_webhook_settings(
        config.webhook,
        host_override=args.host,
        port_override=args.port,
        path_override=args.path,
    )

    if not config.webhook.enabled:
        print(
            "[project-os-agent] WARNING: webhook.enabled is false in config, "
            "but server start was explicitly requested."
        )

    expected_token = os.getenv(config.webhook.secret_env)
    if expected_token is None:
        print(
            f"[project-os-agent] WARNING: env var {config.webhook.secret_env} is not set. "
            "Webhook token validation is disabled."
        )

    handler_cls = _make_webhook_handler(repo_root, config, path, expected_token)
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"[project-os-agent] Webhook listening on http://{host}:{port}{path}")
    log_audit_event(
        repo_root,
        config,
        "command",
        "started",
        command="serve-webhook",
        details={"host": host, "port": port, "path": path, "once": bool(args.once)},
    )

    try:
        if args.once:
            server.handle_request()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("[project-os-agent] Webhook server interrupted")
    finally:
        server.server_close()
        log_audit_event(
            repo_root,
            config,
            "command",
            "stopped",
            command="serve-webhook",
            details={"host": host, "port": port, "path": path},
        )

    return 0

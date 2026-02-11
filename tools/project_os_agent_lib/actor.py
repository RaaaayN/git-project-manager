from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, urlencode

from .audit import log_audit_event
from .config import AgentConfig, ActorOperation, EventPlan, ProposedAction, load_config, safe_text
from .guardrails import (
    GuardrailViolation,
    assert_content_is_safe,
    assert_write_targets_allowed,
    enforce_rate_limit,
    payload_size_from_json,
    safe_write_enabled,
    validate_payload_size,
)
from .pipeline import _infer_event_name_from_payload, _read_payload, process_event_pipeline, sync_docs_from_plan


def _slugify(value: str, max_len: int = 42) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "action")[:max_len]


def _parse_reference_iid(reference: str, prefix: str) -> int | None:
    candidate = reference.strip()
    if not candidate.startswith(prefix):
        return None
    raw_number = candidate[len(prefix) :]
    if raw_number.isdigit():
        return int(raw_number)
    return None


def _extract_issue_iid(plan: EventPlan, action: ProposedAction | None = None) -> int | None:
    targets: list[str] = []
    if action and action.target:
        targets.append(action.target)
    targets.append(plan.normalized_event.reference)
    for target in targets:
        issue_iid = _parse_reference_iid(target, "#")
        if issue_iid is not None:
            return issue_iid
    return None


def _extract_mr_iid(plan: EventPlan) -> int | None:
    return _parse_reference_iid(plan.normalized_event.reference, "!")


def _build_mr_description(action: ProposedAction, plan: EventPlan) -> str:
    context_line = (
        f"Event: {plan.normalized_event.event_type} / {plan.normalized_event.action} "
        f"({plan.normalized_event.reference or 'n/a'})"
    )
    reasoning_lines = [f"- {gap.summary}" for gap in plan.gaps[:4]]
    impact_lines = [f"- {step['description']}" for step in plan.next_steps[:4]]
    if not reasoning_lines:
        reasoning_lines = ["- Keep governance docs synchronized with repo state"]
    if not impact_lines:
        impact_lines = ["- No direct product impact identified"]

    checklist = [
        "- [x] Context analyzed",
        "- [x] Action explained",
        "- [x] Documentation updated or planned",
    ]

    return (
        "Context:\n"
        f"- {context_line}\n"
        f"- Trigger action: {action.summary}\n\n"
        "Reasoning:\n"
        f"{chr(10).join(reasoning_lines)}\n\n"
        "Impact:\n"
        f"{chr(10).join(impact_lines)}\n\n"
        "Checklist:\n"
        f"{chr(10).join(checklist)}\n"
    )


def _build_evidence_block(plan: EventPlan) -> str:
    lines = [f"- {gap.code}: {', '.join(gap.evidence) if gap.evidence else 'n/a'}" for gap in plan.gaps[:5]]
    if not lines:
        lines = ["- no specific evidence"]
    return "\n".join(lines)


class GitLabAPIClient:
    def __init__(self, config: Any, token: str) -> None:
        self.config = config
        self.token = token
        self.project_ref = quote(config.project_id, safe="")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        base_url = f"{self.config.api_url.rstrip('/')}{path}"
        if query:
            base_url = f"{base_url}?{urlencode(query, doseq=True)}"

        body = None
        headers = {"PRIVATE-TOKEN": self.token}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urlrequest.Request(base_url, data=body, headers=headers, method=method.upper())
        try:
            with urlrequest.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitLab API {method} {path} failed: {exc.code} {detail}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"GitLab API {method} {path} failed: {exc}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self.project_ref}/repository/branches",
            payload={"branch": branch, "ref": ref},
        )

    def create_commit(self, branch: str, commit_message: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self.project_ref}/repository/commits",
            payload={"branch": branch, "commit_message": commit_message, "actions": actions},
        )

    def create_merge_request(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self.project_ref}/merge_requests",
            payload={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": True,
            },
        )

    def find_open_issue_by_title(self, title: str) -> dict[str, Any] | None:
        issues = self._request(
            "GET",
            f"/projects/{self.project_ref}/issues",
            query={"state": "opened", "search": title, "per_page": 20},
        )
        if not isinstance(issues, list):
            return None
        for issue in issues:
            if isinstance(issue, dict) and safe_text(issue.get("title")).strip() == title:
                return issue
        return None

    def create_issue(self, title: str, description: str, labels: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "description": description}
        if labels:
            payload["labels"] = ",".join(labels)
        return self._request("POST", f"/projects/{self.project_ref}/issues", payload=payload)

    def update_issue(self, issue_iid: int, description: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/projects/{self.project_ref}/issues/{issue_iid}",
            payload={"description": description},
        )

    def create_issue_note(self, issue_iid: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self.project_ref}/issues/{issue_iid}/notes",
            payload={"body": body},
        )

    def create_mr_note(self, mr_iid: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self.project_ref}/merge_requests/{mr_iid}/notes",
            payload={"body": body},
        )


def _doc_status_map(results: list[Any]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for result in results:
        latest[result.target] = result.status
    return latest


def _build_branch_name(
    config: AgentConfig, plan: EventPlan, action: ProposedAction, index: int
) -> str:
    reference = plan.normalized_event.reference or plan.normalized_event.event_type
    slug = _slugify(f"{action.action_type}-{reference}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return (
        f"{config.gitlab.branch_prefix}/"
        f"{plan.normalized_event.event_type}/"
        f"{index:02d}-{slug}-{timestamp}"
    )


def _build_commit_files_for_action(
    action: ProposedAction,
    doc_statuses: dict[str, str],
) -> list[dict[str, str]]:
    candidate_files: list[str] = []
    if action.action_type == "update_documentation":
        candidate_files = ["PROJECT_STATUS.md"]
    elif action.action_type == "write_adr":
        candidate_files = ["DECISIONS.md"]
    elif action.action_type == "open_merge_request":
        candidate_files = ["PROJECT_STATUS.md", "DECISIONS.md"]

    files: list[dict[str, str]] = []
    for path in candidate_files:
        status = doc_statuses.get(path, "")
        if status in {"created", "would_create"}:
            files.append({"path": path, "change_type": "create"})
        elif status in {"updated", "would_update"}:
            files.append({"path": path, "change_type": "update"})
    return files


def _build_issue_description(action: ProposedAction, plan: EventPlan) -> str:
    lines = [
        "## Context",
        f"- Event: {plan.normalized_event.event_type} / {plan.normalized_event.action}",
        f"- Reference: {plan.normalized_event.reference or 'n/a'}",
        "",
        "## Reasoning",
    ]
    for gap in plan.gaps[:5]:
        lines.append(f"- {gap.summary}")
    lines.extend(
        [
            "",
            "## Proposed Action",
            f"- {action.summary}",
            "",
            "## Evidence",
            _build_evidence_block(plan),
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _build_explanatory_comment(action_summary: str, plan: EventPlan) -> str:
    return (
        f"Project OS Agent action: {action_summary}\n\n"
        "Evidence:\n"
        f"{_build_evidence_block(plan)}\n"
    )


def _build_phase4_operations(
    config: AgentConfig,
    plan: EventPlan,
    doc_results: list[Any],
) -> list[ActorOperation]:
    operations: list[ActorOperation] = []
    doc_statuses = _doc_status_map(doc_results)

    branch_index = 0
    for action in plan.allowed_actions:
        if action.action_type in {"open_merge_request", "update_documentation", "write_adr"}:
            branch_index += 1
            branch_name = _build_branch_name(config, plan, action, branch_index)
            operations.append(
                ActorOperation(
                    op_type="create_branch",
                    action_type=action.action_type,
                    payload={"branch": branch_name, "ref": config.gitlab.target_branch},
                )
            )

            commit_files = _build_commit_files_for_action(action, doc_statuses)
            if commit_files:
                operations.append(
                    ActorOperation(
                        op_type="create_commit",
                        action_type=action.action_type,
                        payload={
                            "branch": branch_name,
                            "commit_message": f"{config.phase4.mr_title_prefix} {action.summary}",
                            "files": commit_files,
                        },
                    )
                )

            operations.append(
                ActorOperation(
                    op_type="open_merge_request",
                    action_type=action.action_type,
                    payload={
                        "branch": branch_name,
                        "title": f"{config.phase4.mr_title_prefix} {action.summary}",
                        "description": _build_mr_description(action, plan),
                    },
                )
            )

            operations.append(
                ActorOperation(
                    op_type="comment_mr",
                    action_type=action.action_type,
                    payload={
                        "branch": branch_name,
                        "body": _build_explanatory_comment(action.summary, plan),
                    },
                )
            )

        if action.action_type == "create_issue":
            operations.append(
                ActorOperation(
                    op_type="upsert_tracking_issue",
                    action_type=action.action_type,
                    payload={
                        "title": f"{config.phase4.issue_title_prefix} {action.summary}",
                        "description": _build_issue_description(action, plan),
                        "labels": config.gitlab.labels,
                    },
                )
            )

        if action.action_type == "comment_issue":
            issue_iid = _extract_issue_iid(plan, action)
            if issue_iid is not None:
                operations.append(
                    ActorOperation(
                        op_type="comment_issue",
                        action_type=action.action_type,
                        payload={
                            "issue_iid": issue_iid,
                            "body": _build_explanatory_comment(action.summary, plan),
                        },
                    )
                )

    if config.phase4.comment_on_source_event:
        if plan.normalized_event.event_type == "issue":
            issue_iid = _extract_issue_iid(plan)
            if issue_iid is not None:
                operations.append(
                    ActorOperation(
                        op_type="comment_issue",
                        action_type="source_event",
                        payload={
                            "issue_iid": issue_iid,
                            "body": _build_explanatory_comment(
                                "Pipeline extract/detect/propose executed",
                                plan,
                            ),
                        },
                    )
                )
        if plan.normalized_event.event_type == "merge_request":
            mr_iid = _extract_mr_iid(plan)
            if mr_iid is not None:
                operations.append(
                    ActorOperation(
                        op_type="comment_source_mr",
                        action_type="source_event",
                        payload={
                            "mr_iid": mr_iid,
                            "body": _build_explanatory_comment(
                                "Pipeline extract/detect/propose executed",
                                plan,
                            ),
                        },
                    )
                )

    return operations


def _commit_actions_payload(
    repo_root: Path,
    config: AgentConfig,
    files: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    payload: list[dict[str, str]] = []
    warnings: list[str] = []
    for file_entry in files:
        path = safe_text(file_entry.get("path"))
        change_type = safe_text(file_entry.get("change_type"))
        if change_type not in {"create", "update"}:
            continue
        abs_path = (repo_root / path).resolve()
        if not abs_path.exists():
            continue
        content = abs_path.read_text(encoding="utf-8")
        try:
            assert_write_targets_allowed(repo_root, config, [path])
            assert_content_is_safe(config, path, content)
        except GuardrailViolation as exc:
            if safe_write_enabled(config):
                warnings.append(f"{path}: {exc}")
                continue
            raise
        payload.append(
            {
                "action": change_type,
                "file_path": path,
                "content": content,
            }
        )
    return payload, warnings


def _execute_phase4_operations(
    repo_root: Path,
    config: AgentConfig,
    operations: list[ActorOperation],
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    branch_to_mr_iid: dict[str, int] = {}

    if not config.phase4.enabled:
        for operation in operations:
            results.append(
                {
                    "op_type": operation.op_type,
                    "action_type": operation.action_type,
                    "status": "skipped_phase4_disabled",
                }
            )
        return results

    if dry_run or not config.gitlab.enabled:
        mode = "dry_run" if dry_run else "skipped_gitlab_disabled"
        for operation in operations:
            results.append(
                {
                    "op_type": operation.op_type,
                    "action_type": operation.action_type,
                    "status": mode,
                    "payload": operation.payload,
                }
            )
        return results

    token = os.getenv(config.gitlab.token_env, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing GitLab token. Set environment variable: {config.gitlab.token_env}"
        )
    client = GitLabAPIClient(config.gitlab, token)

    for operation in operations:
        try:
            if operation.op_type == "create_branch":
                response = client.create_branch(
                    branch=safe_text(operation.payload.get("branch")),
                    ref=safe_text(operation.payload.get("ref")) or config.gitlab.target_branch,
                )
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "result": {
                            "name": response.get("name"),
                            "web_url": response.get("web_url"),
                        },
                    }
                )
                continue

            if operation.op_type == "create_commit":
                file_entries = operation.payload.get("files")
                if not isinstance(file_entries, list):
                    file_entries = []
                actions_payload, warnings = _commit_actions_payload(repo_root, config, file_entries)
                if not actions_payload:
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "skipped_no_files",
                            "warnings": warnings,
                        }
                    )
                    continue
                response = client.create_commit(
                    branch=safe_text(operation.payload.get("branch")),
                    commit_message=safe_text(operation.payload.get("commit_message")),
                    actions=actions_payload,
                )
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "warnings": warnings,
                        "result": {"id": response.get("id"), "short_id": response.get("short_id")},
                    }
                )
                continue

            if operation.op_type == "open_merge_request":
                branch = safe_text(operation.payload.get("branch"))
                response = client.create_merge_request(
                    source_branch=branch,
                    target_branch=config.gitlab.target_branch,
                    title=safe_text(operation.payload.get("title")),
                    description=safe_text(operation.payload.get("description")),
                )
                mr_iid = response.get("iid")
                if isinstance(mr_iid, int):
                    branch_to_mr_iid[branch] = mr_iid
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "result": {"iid": mr_iid, "web_url": response.get("web_url")},
                    }
                )
                continue

            if operation.op_type == "upsert_tracking_issue":
                title = safe_text(operation.payload.get("title"))
                description = safe_text(operation.payload.get("description"))
                labels_raw = operation.payload.get("labels")
                labels = labels_raw if isinstance(labels_raw, list) else []
                existing = client.find_open_issue_by_title(title)
                if existing and isinstance(existing.get("iid"), int):
                    issue_iid = int(existing["iid"])
                    response = client.update_issue(issue_iid=issue_iid, description=description)
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "updated",
                            "result": {"iid": response.get("iid"), "web_url": response.get("web_url")},
                        }
                    )
                else:
                    response = client.create_issue(title=title, description=description, labels=labels)
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "created",
                            "result": {"iid": response.get("iid"), "web_url": response.get("web_url")},
                        }
                    )
                continue

            if operation.op_type == "comment_issue":
                issue_iid = operation.payload.get("issue_iid")
                if not isinstance(issue_iid, int):
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "skipped_invalid_issue_iid",
                        }
                    )
                    continue
                response = client.create_issue_note(
                    issue_iid=issue_iid,
                    body=safe_text(operation.payload.get("body")),
                )
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "result": {"id": response.get("id")},
                    }
                )
                continue

            if operation.op_type == "comment_source_mr":
                mr_iid = operation.payload.get("mr_iid")
                if not isinstance(mr_iid, int):
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "skipped_invalid_mr_iid",
                        }
                    )
                    continue
                response = client.create_mr_note(
                    mr_iid=mr_iid,
                    body=safe_text(operation.payload.get("body")),
                )
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "result": {"id": response.get("id")},
                    }
                )
                continue

            if operation.op_type == "comment_mr":
                branch = safe_text(operation.payload.get("branch"))
                mr_iid = branch_to_mr_iid.get(branch)
                if mr_iid is None:
                    results.append(
                        {
                            "op_type": operation.op_type,
                            "action_type": operation.action_type,
                            "status": "skipped_missing_mr_context",
                        }
                    )
                    continue
                response = client.create_mr_note(
                    mr_iid=mr_iid,
                    body=safe_text(operation.payload.get("body")),
                )
                results.append(
                    {
                        "op_type": operation.op_type,
                        "action_type": operation.action_type,
                        "status": "applied",
                        "result": {"id": response.get("id")},
                    }
                )
                continue

            results.append(
                {
                    "op_type": operation.op_type,
                    "action_type": operation.action_type,
                    "status": "skipped_unknown_operation",
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "op_type": operation.op_type,
                    "action_type": operation.action_type,
                    "status": "error",
                    "error": str(exc),
                }
            )

    return results


def run_act(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    repo_root = config_path.parent.resolve()
    started = time.perf_counter()
    status = "success"
    dry_run = args.dry_run or (config.dry_run and not args.apply)
    plan: EventPlan | None = None
    doc_results: list[Any] = []
    operations: list[ActorOperation] = []
    execution_results: list[dict[str, Any]] = []

    try:
        payload = _read_payload(args)
        validate_payload_size(config, payload_size_from_json(payload), "act")
        enforce_rate_limit(repo_root, config, "act")
        event_name = args.event_name or _infer_event_name_from_payload(payload)
        plan = process_event_pipeline(repo_root, config, event_name, payload)

        if config.phase4.auto_sync_docs and not args.skip_sync_docs:
            doc_results = sync_docs_from_plan(
                repo_root,
                config,
                plan,
                dry_run=dry_run,
                show_diff=not args.no_diff,
            )

        operations = _build_phase4_operations(config, plan, doc_results)
        execution_results = _execute_phase4_operations(
            repo_root=repo_root,
            config=config,
            operations=operations,
            dry_run=dry_run,
        )

        print("[project-os-agent] Phase 4 summary:")
        print(
            json.dumps(
                {
                    "event_type": plan.normalized_event.event_type,
                    "allowed_actions": [action.action_type for action in plan.allowed_actions],
                    "operations_count": len(operations),
                    "execution_results": execution_results,
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
        event_type = plan.normalized_event.event_type if plan else ""
        event_reference = plan.normalized_event.reference if plan else ""
        event_state = plan.normalized_event.state if plan else ""

        log_audit_event(
            repo_root,
            config,
            "command",
            status,
            command="act",
            event_type=event_type,
            reference=event_reference,
            duration_ms=duration_ms,
            details={
                "dry_run": dry_run,
                "event_state": event_state,
                "blocked_actions_count": len(plan.blocked_actions) if plan else 0,
                "operations_count": len(operations),
                "created_count": sum(1 for item in doc_results if item.status == "created"),
                "updated_count": sum(1 for item in doc_results if item.status == "updated"),
            },
        )

        for op_result in execution_results:
            op_type = safe_text(op_result.get("op_type"))
            op_status = safe_text(op_result.get("status")) or "unknown"
            log_audit_event(
                repo_root,
                config,
                "operation",
                op_status,
                command="act",
                event_type=event_type,
                reference=event_reference,
                details={
                    "op_type": op_type,
                    "action_type": safe_text(op_result.get("action_type")),
                    "result": op_result.get("result"),
                    "payload": op_result.get("payload"),
                    "warnings": op_result.get("warnings"),
                    "error": op_result.get("error"),
                },
            )

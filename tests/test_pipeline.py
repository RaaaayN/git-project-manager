"""
Unit tests for Project OS Agent pipeline: event normalization, gap detection,
action proposal, and config loading.

Tests cover: normalize_gitlab_event, detect_gaps, propose_actions, apply_policy, load_config.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.config import (  # noqa: E402
    BlockedAction,
    Gap,
    NormalizedEvent,
    PolicyConfig,
    ProposedAction,
    load_config,
)
from project_os_agent_lib.pipeline import (  # noqa: E402
    apply_policy,
    detect_gaps,
    normalize_gitlab_event,
    propose_actions,
)


# --- Fixtures: minimal GitLab payloads ---

def _payload_mr_merged() -> dict:
    return {
        "object_kind": "merge_request",
        "object_attributes": {
            "action": "merge",
            "state": "merged",
            "title": "Add feature X",
            "iid": 42,
            "url": "https://gitlab.com/group/project/-/merge_requests/42",
        },
        "project": {"path_with_namespace": "group/project", "web_url": "https://gitlab.com/group/project"},
        "user": {"username": "alice", "name": "Alice"},
    }


def _payload_issue() -> dict:
    return {
        "object_kind": "issue",
        "object_attributes": {
            "action": "open",
            "state": "opened",
            "title": "Fix bug in auth",
            "iid": 123,
            "url": "https://gitlab.com/group/project/-/issues/123",
        },
        "project": {"path_with_namespace": "group/project"},
        "user": {"username": "bob"},
    }


def _payload_pipeline_failed() -> dict:
    return {
        "object_kind": "pipeline",
        "object_attributes": {
            "status": "failed",
            "id": 456,
            "url": "https://gitlab.com/group/project/-/pipelines/456",
        },
        "project": {"path_with_namespace": "group/project"},
    }


# --- T1: Tests for normalize_gitlab_event ---

class NormalizeGitLabEventTests(unittest.TestCase):
    def test_mr_merged_payload(self) -> None:
        payload = _payload_mr_merged()
        event = normalize_gitlab_event("Merge Request Hook", payload)
        self.assertEqual(event.event_type, "merge_request")
        self.assertEqual(event.action, "merge")
        self.assertEqual(event.state, "merged")
        self.assertEqual(event.reference, "!42")
        self.assertEqual(event.title, "Add feature X")
        self.assertEqual(event.project, "group/project")
        self.assertEqual(event.author, "alice")

    def test_issue_payload(self) -> None:
        payload = _payload_issue()
        event = normalize_gitlab_event("Issue Hook", payload)
        self.assertEqual(event.event_type, "issue")
        self.assertEqual(event.action, "open")
        self.assertEqual(event.state, "opened")
        self.assertEqual(event.reference, "#123")
        self.assertEqual(event.title, "Fix bug in auth")

    def test_pipeline_failed_payload(self) -> None:
        payload = _payload_pipeline_failed()
        event = normalize_gitlab_event("Pipeline Hook", payload)
        self.assertEqual(event.event_type, "pipeline")
        self.assertEqual(event.state, "failed")
        self.assertEqual(event.action, "failed")
        self.assertEqual(event.reference, "pipeline:456")

    def test_event_name_inferred_from_object_kind_when_empty(self) -> None:
        payload = _payload_mr_merged()
        event = normalize_gitlab_event("", payload)
        self.assertEqual(event.event_name, "Merge Request Hook")
        self.assertEqual(event.event_type, "merge_request")

        payload = _payload_issue()
        event = normalize_gitlab_event("", payload)
        self.assertEqual(event.event_name, "Issue Hook")
        self.assertEqual(event.event_type, "issue")

        payload = _payload_pipeline_failed()
        event = normalize_gitlab_event("", payload)
        self.assertEqual(event.event_name, "Pipeline Hook")
        self.assertEqual(event.event_type, "pipeline")

    def test_payload_non_dict_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            normalize_gitlab_event("Merge Request Hook", "not a dict")
        self.assertIn("Webhook payload must be a JSON object", str(ctx.exception))

        with self.assertRaises(ValueError):
            normalize_gitlab_event("", None)
        with self.assertRaises(ValueError):
            normalize_gitlab_event("", [])
        with self.assertRaises(ValueError):
            normalize_gitlab_event("", 123)


# --- T2: Tests for detect_gaps ---

def _make_event(
    event_type: str,
    action: str = "unknown",
    state: str = "unknown",
    reference: str = "",
    title: str = "",
    event_name: str = "Unknown",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_name=event_name,
        event_type=event_type,
        action=action,
        state=state,
        title=title,
        author="",
        url="",
        project="",
        reference=reference,
        payload={},
    )


class DetectGapsTests(unittest.TestCase):
    def test_missing_critical_docs_gap(self) -> None:
        event = _make_event(event_type="merge_request")
        context = {"missing_critical_docs": ["PRODUCT_SPEC.md", "AGENTS.md"]}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("docs_missing", codes)
        gap = next(g for g in gaps if g.code == "docs_missing")
        self.assertEqual(gap.severity, "high")
        self.assertIn("PRODUCT_SPEC.md", gap.evidence)

    def test_stale_docs_gap(self) -> None:
        event = _make_event(event_type="issue")
        context = {
            "missing_critical_docs": [],
            "stale_docs": [
                {"file": "PROJECT_STATUS.md", "age_days": 20, "last_modified": "2025-01-01"},
            ],
        }
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("docs_stale", codes)
        gap = next(g for g in gaps if g.code == "docs_stale")
        self.assertEqual(gap.severity, "medium")

    def test_mr_merged_gaps(self) -> None:
        event = _make_event(
            event_type="merge_request",
            action="merge",
            state="merged",
            reference="!42",
            title="Refactor auth module",
        )
        context = {"missing_critical_docs": [], "stale_docs": []}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("post_merge_sync_required", codes)
        # "auth" in title triggers decision_record_missing
        self.assertIn("decision_record_missing", codes)

    def test_mr_merged_no_decision_gap_when_title_does_not_match(self) -> None:
        event = _make_event(
            event_type="merge_request",
            action="merge",
            state="merged",
            reference="!42",
            title="Fix typo in README",
        )
        context = {"missing_critical_docs": [], "stale_docs": []}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("post_merge_sync_required", codes)
        self.assertNotIn("decision_record_missing", codes)

    def test_issue_short_description_gap(self) -> None:
        event = _make_event(event_type="issue", reference="#123")
        context = {"issue_description": "Bug"}  # < 60 chars
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("issue_context_missing", codes)

    def test_issue_acceptance_criteria_missing_gap(self) -> None:
        event = _make_event(event_type="issue", reference="#123")
        context = {
            "issue_description": "This is a reasonably long description that exceeds sixty characters for sure.",
        }
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("issue_acceptance_missing", codes)

    def test_issue_with_acceptance_criteria_no_gap(self) -> None:
        event = _make_event(event_type="issue", reference="#123")
        context = {
            "issue_description": "Long enough description. Acceptance criteria: - [ ] Tests pass",
        }
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertNotIn("issue_acceptance_missing", codes)
        self.assertNotIn("issue_context_missing", codes)

    def test_pipeline_failed_gaps(self) -> None:
        event = _make_event(
            event_type="pipeline",
            action="failed",
            state="failed",
            reference="pipeline:456",
        )
        context = {"project_status_mentions_at_risk": False}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("pipeline_failure", codes)
        self.assertIn("risk_not_reflected", codes)

    def test_pipeline_failed_no_risk_gap_when_at_risk_mentioned(self) -> None:
        event = _make_event(
            event_type="pipeline",
            action="failed",
            state="failed",
        )
        context = {"project_status_mentions_at_risk": True}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("pipeline_failure", codes)
        self.assertNotIn("risk_not_reflected", codes)

    def test_unsupported_event_gap(self) -> None:
        event = _make_event(
            event_type="unknown",
            event_name="Deployment Hook",
        )
        context = {}
        gaps = detect_gaps(event, context)
        codes = [g.code for g in gaps]
        self.assertIn("unsupported_event", codes)
        gap = next(g for g in gaps if g.code == "unsupported_event")
        self.assertEqual(gap.severity, "low")


# --- T3: Tests for propose_actions and apply_policy ---

class ProposeActionsTests(unittest.TestCase):
    def test_missing_docs_proposes_open_merge_request(self) -> None:
        event = _make_event(event_type="merge_request", action="merge", state="merged")
        context = {"missing_critical_docs": ["PRODUCT_SPEC.md"]}
        gaps = [
            Gap(code="docs_missing", severity="high", summary="Docs missing", evidence=["PRODUCT_SPEC.md"]),
        ]
        actions = propose_actions(event, context, gaps)
        action_types = [a.action_type for a in actions]
        self.assertIn("open_merge_request", action_types)
        bootstrap = next(a for a in actions if a.action_type == "open_merge_request")
        self.assertIn("PRODUCT_SPEC.md", bootstrap.details.get("missing_files", []))

    def test_pipeline_failed_proposes_create_issue_and_update_documentation(self) -> None:
        event = _make_event(
            event_type="pipeline",
            action="failed",
            state="failed",
            reference="pipeline:456",
        )
        context = {}
        gaps = [
            Gap(code="pipeline_failure", severity="high", summary="Pipeline failed", evidence=["pipeline:456"]),
        ]
        actions = propose_actions(event, context, gaps)
        action_types = [a.action_type for a in actions]
        self.assertIn("create_issue", action_types)
        self.assertIn("update_documentation", action_types)


class ApplyPolicyTests(unittest.TestCase):
    def test_forbidden_action_blocked_with_explicit_reason(self) -> None:
        action = ProposedAction(
            action_type="delete_file",
            summary="Delete temp file",
            target="tmp.txt",
            details={},
        )
        policy = PolicyConfig(
            allowed_actions={"open_merge_request", "update_documentation"},
            forbidden_actions={"delete_file", "direct_code_modification"},
        )
        allowed, blocked = apply_policy([action], policy)
        self.assertEqual(len(allowed), 0)
        self.assertEqual(len(blocked), 1)
        self.assertIn("forbidden", blocked[0].reason.lower())
        self.assertEqual(blocked[0].action.action_type, "delete_file")

    def test_action_not_in_allowed_blocked(self) -> None:
        action = ProposedAction(
            action_type="open_merge_request",
            summary="Bootstrap docs",
            target="docs/bootstrap",
            details={},
        )
        policy = PolicyConfig(
            allowed_actions={"update_documentation"},  # open_merge_request not allowed
            forbidden_actions=set(),
        )
        allowed, blocked = apply_policy([action], policy)
        self.assertEqual(len(allowed), 0)
        self.assertEqual(len(blocked), 1)
        self.assertIn("not allowed", blocked[0].reason.lower())

    def test_allowed_action_passes(self) -> None:
        action = ProposedAction(
            action_type="update_documentation",
            summary="Sync status",
            target="PROJECT_STATUS.md",
            details={},
        )
        policy = PolicyConfig(
            allowed_actions={"update_documentation", "write_adr"},
            forbidden_actions={"delete_file"},
        )
        allowed, blocked = apply_policy([action], policy)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(len(blocked), 0)
        self.assertEqual(allowed[0].action_type, "update_documentation")


# --- T4: Tests for load_config ---

class LoadConfigTests(unittest.TestCase):
    def test_valid_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            self.assertEqual(config.version, 1)
            self.assertEqual(config.templates_dir, "templates")
            self.assertTrue(config.dry_run)
            self.assertGreater(len(config.managed_files), 0)

    def test_missing_config_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "nonexistent.yml"
            with self.assertRaises(FileNotFoundError) as ctx:
                load_config(missing_path)
            self.assertIn("nonexistent", str(ctx.exception))

    def test_unknown_action_in_allowed_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            base_config = (REPO_ROOT / ".project-os-agent.yml").read_text(encoding="utf-8")
            # Inject unknown action
            invalid_config = base_config.replace(
                "- open_merge_request",
                "- open_merge_request\n    - invalid_action_type",
            )
            config_path.write_text(invalid_config, encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_config(config_path)
            self.assertIn("invalid_action_type", str(ctx.exception))
            self.assertIn("Unknown action", str(ctx.exception))

    def test_templates_dir_outside_repo_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            base_config = (REPO_ROOT / ".project-os-agent.yml").read_text(encoding="utf-8")
            invalid_config = base_config.replace("templates_dir: templates", "templates_dir: ..")
            config_path.write_text(invalid_config, encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_config(config_path)
            self.assertIn("templates_dir", str(ctx.exception))
            self.assertIn("inside the repository", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

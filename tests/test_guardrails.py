"""
Unit tests for Project OS Agent guardrails: payload size, secret detection,
write targets, rate limiting, and safe-write config.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.config import load_config  # noqa: E402
from project_os_agent_lib.guardrails import (  # noqa: E402
    GuardrailViolation,
    assert_content_is_safe,
    assert_write_targets_allowed,
    validate_payload_size,
    safe_write_enabled,
    enforce_rate_limit,
)


class ValidatePayloadSizeTests(unittest.TestCase):
    def test_payload_under_limit_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            validate_payload_size(config, 1000, "test")
            validate_payload_size(config, config.phase5.guardrails.max_payload_bytes, "test")

    def test_payload_over_limit_raises_guardrail_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", config_path)
            raw = config_path.read_text(encoding="utf-8").replace("524288", "1024")
            config_path.write_text(raw, encoding="utf-8")
            config = load_config(config_path)
            with self.assertRaises(GuardrailViolation) as ctx:
                validate_payload_size(config, 2048, "webhook")
            self.assertIn("Payload too large", str(ctx.exception))
            self.assertIn("2048", str(ctx.exception))


class AssertContentIsSafeTests(unittest.TestCase):
    def test_content_without_secret_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            assert_content_is_safe(config, "README.md", "Normal project documentation.")
            assert_content_is_safe(config, "PROJECT_STATUS.md", "# Status\n\nAll good.")

    def test_content_with_glpat_secret_raises_guardrail_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            with self.assertRaises(GuardrailViolation) as ctx:
                assert_content_is_safe(config, "config.yml", "token: glpat-abcdefghij123456")
            self.assertIn("Secret-like content", str(ctx.exception))
            self.assertIn("glpat-", str(ctx.exception))


class AssertWriteTargetsAllowedTests(unittest.TestCase):
    def test_allowed_managed_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            assert_write_targets_allowed(temp_root, config, ["PROJECT_STATUS.md"])
            assert_write_targets_allowed(temp_root, config, ["DECISIONS.md", "README.md"])

    def test_blocked_path_raises_guardrail_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            with self.assertRaises(GuardrailViolation) as ctx:
                assert_write_targets_allowed(temp_root, config, [".env"])
            self.assertIn("blocked", str(ctx.exception).lower())

            with self.assertRaises(GuardrailViolation):
                assert_write_targets_allowed(temp_root, config, [".git/HEAD"])
            with self.assertRaises(GuardrailViolation):
                assert_write_targets_allowed(temp_root, config, ["secrets/api.key"])


class SafeWriteEnabledTests(unittest.TestCase):
    def test_safe_write_enabled_when_phase5_and_guardrails_on(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")
            config = load_config(temp_root / ".project-os-agent.yml")
            self.assertTrue(safe_write_enabled(config))

    def test_safe_write_disabled_when_phase5_off(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", config_path)
            raw = config_path.read_text(encoding="utf-8").replace(
                "phase5:\n  enabled: true", "phase5:\n  enabled: false"
            )
            config_path.write_text(raw, encoding="utf-8")
            config = load_config(config_path)
            self.assertFalse(safe_write_enabled(config))


class EnforceRateLimitTests(unittest.TestCase):
    def test_rate_limit_exceeded_raises_guardrail_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            config_path = temp_root / ".project-os-agent.yml"
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", config_path)
            raw = config_path.read_text(encoding="utf-8").replace(
                "max_events: 30", "max_events: 2"
            ).replace("window_seconds: 60", "window_seconds: 60")
            config_path.write_text(raw, encoding="utf-8")
            config = load_config(config_path)

            state_dir = temp_root / ".project-os-agent"
            state_dir.mkdir(parents=True, exist_ok=True)
            state_path = state_dir / "rate-limit.json"
            import time
            now = time.time()
            state_path.write_text(
                json.dumps({"process-event": [now, now, now]}),
                encoding="utf-8",
            )

            with self.assertRaises(GuardrailViolation) as ctx:
                enforce_rate_limit(temp_root, config, "process-event")
            self.assertIn("Rate limit exceeded", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

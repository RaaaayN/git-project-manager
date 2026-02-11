from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from project_os_agent_lib.config import load_config  # noqa: E402
from project_os_agent_lib.pipeline import create_missing_files  # noqa: E402


def _render_with_placeholders(template_text: str, placeholders: dict[str, str]) -> str:
    rendered = template_text
    for token, value in placeholders.items():
        rendered = rendered.replace(token, value)
    return rendered


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TemplateRegressionTests(unittest.TestCase):
    def test_template_hash_snapshot(self) -> None:
        snapshot_path = REPO_ROOT / "tests" / "template_snapshots.json"
        expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

        templates_dir = REPO_ROOT / "templates"
        current = {path.name: _sha256(path) for path in sorted(templates_dir.glob("*.md"))}

        self.assertEqual(expected, current)

    def test_bootstrap_creates_files_with_placeholder_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            config = load_config(temp_root / ".project-os-agent.yml")
            results = create_missing_files(temp_root, config, dry_run=False)

            created = [item for item in results if item.status == "created"]
            self.assertEqual(len(created), len(config.managed_files))

            for mapping in config.managed_files:
                template_path = temp_root / "templates" / mapping.template
                target_path = temp_root / mapping.target
                expected_content = _render_with_placeholders(
                    template_path.read_text(encoding="utf-8"),
                    config.placeholders,
                )
                self.assertTrue(target_path.exists(), f"Missing target file: {mapping.target}")
                self.assertEqual(expected_content, target_path.read_text(encoding="utf-8"))

    def test_bootstrap_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(REPO_ROOT / "templates", temp_root / "templates")
            shutil.copy2(REPO_ROOT / ".project-os-agent.yml", temp_root / ".project-os-agent.yml")

            config = load_config(temp_root / ".project-os-agent.yml")
            results = create_missing_files(temp_root, config, dry_run=True)

            would_create = [item for item in results if item.status == "would_create"]
            self.assertEqual(len(would_create), len(config.managed_files))
            for mapping in config.managed_files:
                self.assertFalse((temp_root / mapping.target).exists(), mapping.target)


if __name__ == "__main__":
    unittest.main()

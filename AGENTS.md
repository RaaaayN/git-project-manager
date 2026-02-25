# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Project OS Agent is a Python CLI tool and webhook server for AI-driven GitLab project governance. It has no frontend — all interaction is via CLI commands and HTTP endpoints.

### Dependencies

- **Python 3.10+** (system Python 3.12 works)
- **PyYAML** — the only external dependency; install via `pip install -r requirements.txt` or `pip install pyyaml`

### Running tests

```
python -m unittest discover -s tests -v
```

Or run specific suites, for example: `python -m unittest tests.test_template_regression tests.test_pipeline tests.test_kpi -v`

### Running the CLI

All commands are invoked from the repo root via `python tools/project_os_agent.py <command>`:

| Command | Example |
|---------|---------|
| `bootstrap` | `python3 tools/project_os_agent.py bootstrap --dry-run` |
| `process-event` | `python3 tools/project_os_agent.py process-event --payload-json '{...}'` |
| `sync-docs` | `python3 tools/project_os_agent.py sync-docs --payload-json '{...}'` |
| `act` | `python3 tools/project_os_agent.py act --payload-json '{...}'` |
| `serve-webhook` | `python3 tools/project_os_agent.py serve-webhook --port 8080` |
| `report-kpis` | `python3 tools/project_os_agent.py report-kpis --stdout-only` |

### Running the webhook server

```
python tools/project_os_agent.py serve-webhook --port 8080
```

Use `--once` flag for single-request testing. The server listens on `/webhooks/gitlab` by default.

### Gotchas

- The config file `.project-os-agent.yml` has `dry_run: true` by default. Pass `--apply` to write files.
- GitLab and LLM integrations are disabled by default (`gitlab.enabled: false`, `phase3.llm.enabled: false`). They require `GITLAB_TOKEN` / `GEMINI_API_KEY` env vars respectively.
- The `__pycache__` directory under `tools/` may contain stale `.pyc` files from a different Python version — safe to ignore or delete.
- There is no linter configured in the repo. Code style checks are not enforced via CI.

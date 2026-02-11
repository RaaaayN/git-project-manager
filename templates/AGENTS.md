# AGENTS.md

## Project OS Agent

### Role
Ensures consistency between:
- PRODUCT_SPEC.md
- PROJECT_STATUS.md
- Source code
- CI pipeline

### Triggers
- Merge Request merged
- Issue created or updated
- Pipeline failure

### Allowed Actions
- Open Merge Requests
- Update documentation files
- Comment on issues and MRs

### Forbidden Actions
- Direct code modification without MR
- Deleting files
- Modifying architecture without documentation update

---

## Documentation Rules

- All .md updates must be done via MR
- All automated changes must include explanation

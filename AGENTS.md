# Repository Guidelines

## Project Structure & Module Organization
This is a Django prototype. Source code is organized by app directories at the repo root, including `accounts`, `projects`, `chats`, `objects`, `config`, `config_ui`, `imports`, `notifications`, and `uploads`. Project settings and URL routing live in `workbench/`. Shared templates live in `templates/`, static assets in `static/`, and user-generated media in `Media/` or `media/` (see `MEDIA_ROOT` in settings). The database is SQLite (`db.sqlite3`).

## Build, Test, and Development Commands
- `python -m venv .venv` creates a local virtual environment.
- `.venv\Scripts\Activate.ps1` activates the venv (PowerShell).
- `pip install django djangorestframework certifi` installs dependencies.
- `python manage.py migrate` applies migrations.
- `python manage.py check` runs Django system checks.
- `python manage.py seed_initial_data` seeds roles, admin user, and starter data.
- `python manage.py runserver` starts the dev server.

## Coding Style & Naming Conventions
Python code follows standard Django conventions. Use 4-space indentation, snake_case for functions and variables, and PascalCase for classes and models. Keep modules app-scoped (e.g., `projects/services_*.py`). Avoid non-ASCII in comments unless the file already uses it.

## Testing Guidelines
No formal test suite is present in this repo. If you add tests, use Django’s default test runner (`python manage.py test`) and place tests in `tests.py` or a `tests/` package per app. Keep naming consistent with Django conventions.

## Commit & Pull Request Guidelines
Commit history uses short, descriptive, sentence-style messages (e.g., “Sandbox slice completed.”). Prefer clear, plain-English summaries without prefixes. PR expectations are not formalized; include a brief description, key changes, and any manual testing performed (e.g., “ran `python manage.py check`”).

## Security & Configuration Notes
Email configuration is environment-driven: set `DJANGO_ENV`, `EMAIL_HOST_USER`, and `EMAIL_HOST_PASSWORD` as needed. In dev, set `DJANGO_ENV="dev"` to use console email output. If using AVG, disable Email Shield to avoid TLS issues.


Interaction rules

British English

short, step-gated changes

ask before broad refactors

Safety rails

show diffs; don’t apply automatically

don’t touch .venv/, static/, media/, node_modules/

no drive-by formatting / reordering unless requested

Your repo conventions

7-bit ASCII in templates/comments (if that’s a real constraint for your toolchain)

keep URL names/paths stable unless explicitly told

prefer moving code without changing behaviour

Codex merges project instructions it finds along the path from repo root to your working directory, with later/closer files overriding earlier ones.

## Review Conference Notes (Artefact Glossary + Structure)
Artefact Glossary + Structure (excerpt):
- Anchor: the single artefact for a marker.
- Marker: INTENT, ROUTE, EXECUTE, COMPLETE.
- Review Conference: per project per user chat for one marker.
- CKO: Canonical Knowledge Object (INTENT anchor).
- WKO: Workflow Knowledge Object (execution artefact, not in this slice).
- TKO: Transfer Knowledge Object (handoff record).
- PKO: Policy Knowledge Object (policy record).
- Do not interpret CKO as course kick-off.
- Use structures as guidance, not bureaucracy.
- Never add filler text to populate sections.

Lightweight canonical structures (sections optional):
- CKO sections: Canonical summary (<=10 words), Scope, Statement, Supporting basis, Assumptions, Alternatives considered, Uncertainties / limits, Provenance.
- WKO sections: Purpose, Current state, Open questions, Options / candidate approaches, Risks / dependencies, Next actions, Provenance.
- TKO sections: Canonical summary (<=10 words), Working preferences, Context / why this exists, Current state, Decisions made (and why), In scope next, Out of scope, Known risks / gotchas, Files / modules / commands, Next step (single, concrete).
- PKO sections: Policy summary (<=10 words), Policy statement, Rationale, Applies to, Does not apply to, Enforcement, Exceptions, Versioning / provenance.
## End Review Conference Notes

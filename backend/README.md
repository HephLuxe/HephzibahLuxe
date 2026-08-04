# Hephzibah Luxe — backend


## Stack

Python 3.11 · Django 5.2 · Django REST Framework · SimpleJWT · PostgreSQL ·
Celery + Redis (`django-celery-beat`) · Cloudflare R2 (S3-compatible media) ·
Brevo (transactional email) · Gunicorn + WhiteNoise · Sentry/GlitchTip + Loki.

## Layout

- `config/` — Django project (settings, urls, wsgi/asgi, celery).
- `apps/` — 13 domain apps, each with its own `README.md` (start there): `accounts`,
  `core` (shared utilities: permissions, pagination, storages, observability, …),
  `portal`, `events`, `meetings`, `contacts`, `conversations`, `reminders`,
  `documents`, `document_hub`, `budgets`, `notifications`, `inquiries`.
- `docs/` — `API_CONTRACT.md` (route list), `OBSERVABILITY_STANDARD.md`,
  `FAILURE_POINTS_AUDIT.md`, and `brevo-templates/`.
- `RUNBOOK.md` — the operator guide (bootstrap, deploy, Celery, common tasks).

## Quickstart (local dev)

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                                 # then fill in the REQUIRED keys
python manage.py migrate
python manage.py seed_periodic_tasks
python manage.py createsuperuser
python manage.py runserver
```

The app **fails fast at boot** if a required env key is missing — see the
documented list in [`.env.example`](.env.example). Everything is mounted under
`/api/v1/`; admin at `/admin/`; health probes at `/health/` and `/health/ready/`.

## Tests & quality

```bash
pytest                      # full suite (see pytest.ini / conftest.py)
pytest --cov=apps           # with coverage
ruff check .                # lint (config in pyproject.toml)
ruff format .               # format
python manage.py check --deploy   # deploy readiness (run with DEBUG=False)
```

CI (`.github/workflows/ci.yml`) runs the migration-drift check, deploy checks, and
the test suite against Postgres + Redis on every push/PR.

## Deploy

Railway, three services from this one repo (`web` / `worker` / `beat`) — see
[`RUNBOOK.md`](RUNBOOK.md) for the full steps. Media (client documents **and**
event pictures) must be served from Cloudflare R2 in production: Railway's disk is
ephemeral, so set `USE_R2_STORAGE=True` and the R2 keys. See the storage notes in
[`.env.example`](.env.example) and `apps/core/storages.py`.

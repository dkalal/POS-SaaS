# POS SaaS

Multi-tenant point-of-sale SaaS built with Django, PostgreSQL, and Redis.

## Operations at a glance

The operational source of truth is the [Admin Runbook](docs/ADMIN_RUNBOOK.md).

| Need | Guide |
| --- | --- |
| Deploy or roll back a release | [Deploy a release](docs/ADMIN_RUNBOOK.md#4-deploying-a-release) |
| Take or validate a backup | [Backup operations](docs/ADMIN_RUNBOOK.md#5-backup-operations) |
| Restore data or run a recovery drill | [Restore and recovery](docs/ADMIN_RUNBOOK.md#6-restore-and-disaster-recovery) |
| Respond to an incident | [Common issues](docs/ADMIN_RUNBOOK.md#8-common-issues-and-first-response) |
| PostgreSQL tuning and audit integrity | [PostgreSQL operations](docs/POSTGRES_OPERATIONS.md) |

## Local setup

1. Create the virtual environment:

```powershell
python -m venv .venv
```

2. Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

4. Start PostgreSQL locally. The repo ships with a Dockerized database that uses the same credentials as `.env`:

```powershell
docker compose up -d db
```

5. Run the project locally:

```powershell
python manage.py migrate
python manage.py runserver
```

The local venv now uses PostgreSQL too, via `.env`, and the Dockerized database is exposed on `127.0.0.1:55432` to avoid colliding with the host PostgreSQL service already running on `5432` and the other local Docker databases already using `5433` and `5434`. If you want to point at a different local Postgres instance, update `DATABASE_URL` before running `migrate`.

## Docker development

```powershell
docker compose up --build
```

Docker uses PostgreSQL through the `DATABASE_URL` passed in `docker-compose.yml` and publishes it on `55432` locally. The web app is available at `http://127.0.0.1:8002` by default, avoiding the common local port `8000`; set `POS_SAAS_WEB_PORT` to use another available port.

### Trusted LAN access

For a trusted local network, this workstation includes an automatic Compose override:

```powershell
docker compose up -d --build
```

`docker-compose.override.yml` makes the app available at `http://10.10.10.254:8002/`, adds that exact host to Django's allowlist, and leaves PostgreSQL localhost-only. Docker automatically applies this file to normal `docker compose` commands, including rebuilds. For another network, replace only the host IP. Restrict inbound TCP 8002 to the trusted subnet in Windows Firewall; do not use this development configuration as an Internet-facing deployment.

This is deliberately a development stack: it uses `runserver`, bind-mounts the source, and starts a local PostgreSQL container. It must not be deployed to production.

## Production deployment

Follow the verified [release procedure](docs/ADMIN_RUNBOOK.md#4-deploying-a-release)
for the pre-flight checks, migration, readiness gate, acceptance checks, and
rollback. The runbook also covers backup, restore, and incident response.

Build one immutable image and deploy it behind a TLS-terminating reverse proxy or load balancer. The app is intentionally bound to `127.0.0.1:8000` in the supplied production Compose file, so the public proxy is the only component that can reach it.

```bash
# .env.production contains configuration only; secrets are mounted from SECRETS_DIR.
docker compose --env-file .env.production -f docker-compose.production.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.production.yml up -d web
```

Use managed PostgreSQL and Redis, both on private networks. Do not publish their ports. The deployment identity needs read-only access to `SECRETS_DIR`; set `_FILE` settings such as `DJANGO_SECRET_KEY_FILE=/run/secrets/django_secret_key` and `DATABASE_URL_FILE=/run/secrets/database_url`.

Start from [.env.production.example](.env.production.example). The database URL must use `sslmode=verify-full` and reference the mounted database CA where required. The container image and production environment file should be versioned/reviewed; the secret directory must not be.

The platform should probe `GET /healthz/` for liveness and `GET /readyz/` for readiness. Readiness checks both the configured database and cache; do not route traffic until it returns `200`. Run migrations once per release, before a web rollout, and schedule `python manage.py verify_audit_log` outside the web container.

The application currently has no user-upload feature. Before introducing product images, imports, or generated documents, configure private object storage and tenant-scoped signed downloads—never container-local media storage.

## Email delivery

Local development uses Django's console email backend by default, so invitation emails print in the server logs.

For SendGrid SMTP delivery, set these environment variables:

```powershell
SENDGRID_API_KEY=your-sendgrid-api-key
DJANGO_DEFAULT_FROM_EMAIL="POS SaaS <verified-sender@example.com>"
```

`DJANGO_DEFAULT_FROM_EMAIL` must use a sender or domain identity that is verified in SendGrid. When `SENDGRID_API_KEY` is set, the app automatically uses `smtp.sendgrid.net` on port `587` with TLS and the standard SendGrid SMTP username `apikey`.

Self-service signup requires email verification only when a deliverable email backend is configured. The default console backend does not pretend to deliver verification mail and allows the new owner to continue directly into the tenant-scoped onboarding checklist.
Set `DJANGO_OUTBOUND_EMAIL_ENABLED=1` only after a custom non-SendGrid backend has been verified end to end; the SendGrid SMTP overlay enables it automatically.

## Production security

Set `DJANGO_ENVIRONMENT=production`. Production startup intentionally fails if debug is enabled, the secret key is weak, hosts are broad, the public URL is not HTTPS, PostgreSQL or shared Redis is missing, secure cookies/HTTPS redirect are disabled, or outbound email is not deliverable. Secrets can be supplied as environment variables or with their matching `_FILE` variable (for example, `DJANGO_SECRET_KEY_FILE` and `DATABASE_URL_FILE`).

Terminate TLS at the application server or a trusted reverse proxy. Enable `DJANGO_TRUST_X_FORWARDED_PROTO=1` only when that proxy strips and replaces the header, and list its direct address in `DJANGO_TRUSTED_PROXY_IPS` so client-IP rate limits cannot be spoofed. Set exact `DJANGO_ALLOWED_HOSTS`, the canonical `DJANGO_PUBLIC_BASE_URL`, and any genuinely cross-origin form origins in `DJANGO_CSRF_TRUSTED_ORIGINS`.

Before each release, run:

```bash
python manage.py check --deploy
python manage.py test
python -m pip check
```

See [SECURITY.md](SECURITY.md) for the audit findings, deployment assumptions, rate-limit policy, and residual risks.

# POS SaaS security baseline

## Audit summary (2026-07-20)

The application already had Django CSRF middleware, CSRF tokens on the audited state-changing forms, HTTP-only SameSite cookies, hashed single-use invitation and verification tokens, tenant-scoped authorization, POST-only logout and sensitive team actions, password validation, and API-key throttling.

The hardening pass addressed these high-priority gaps:

- Django 5.1 was outside security support. The project now targets Django 5.2 LTS and Django REST framework 3.16.
- Production could start with `dev-secret-key`, local-only hosts, SQLite, per-process cache, console email, or insecure HTTP assumptions. `DJANGO_ENVIRONMENT=production` now validates and rejects these configurations.
- `.env` could be copied into the container build context/image. `.dockerignore` excludes it, and the application supports `*_FILE` secrets.
- Login had no abuse controls; signup counted only successful validation; invitations were not limited. Shared-cache limits now protect login by independent IP and identity buckets, signup by IP, invitations by tenant/actor/IP, invitation-token POSTs, and verification resends.
- Absolute email links depended on the inbound request. Production requires a canonical HTTPS `DJANGO_PUBLIC_BASE_URL`.
- Error pages and CSRF failures now return generic HTML or JSON without stack traces or validation internals, while Django and security events go to structured console logs for the deployment log collector.
- The production container now runs as an unprivileged user and uses Gunicorn rather than Django's development server.

## Required production settings

Use explicit hostnames and secrets unique to the environment. A minimal shape is:

```dotenv
DJANGO_ENVIRONMENT=production
DJANGO_DEBUG=0
DJANGO_SECRET_KEY_FILE=/run/secrets/django_secret_key
DJANGO_ALLOWED_HOSTS=pos.example.com
DJANGO_PUBLIC_BASE_URL=https://pos.example.com
DATABASE_URL_FILE=/run/secrets/database_url
DJANGO_CACHE_URL=rediss://:password@redis.internal:6379/1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_SECURE_SSL_REDIRECT=1
SENDGRID_API_KEY_FILE=/run/secrets/sendgrid_api_key
DJANGO_DEFAULT_FROM_EMAIL=POS SaaS <no-reply@example.com>
```

Use `DJANGO_CSRF_TRUSTED_ORIGINS=https://admin.example.com` only if a different trusted origin genuinely submits forms. It is not an alternative to `ALLOWED_HOSTS`.

When TLS terminates at a reverse proxy, configure all of the following only after verifying the proxy strips client-supplied forwarding headers:

```dotenv
DJANGO_TRUST_X_FORWARDED_PROTO=1
DJANGO_TRUSTED_PROXY_IPS=10.0.0.10
DJANGO_TRUSTED_PROXY_COUNT=1
```

HSTS defaults to one year in production. Enable `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` only after confirming every subdomain is HTTPS-capable. Enable preload only after meeting the browser preload requirements; both irreversible-scope options remain off by default.

## Default rate limits

| Flow | Buckets | Limit |
| --- | --- | --- |
| Failed login | IP and normalized identity (independent) | 20/IP and 5/identity per 15 minutes |
| Signup | IP | 10/hour |
| Create/resend invitation | tenant, actor, and IP | 20/hour |
| Accept/create invited account | token and IP | 10/15 minutes |
| Resend verification | user and IP | 3/hour |
| Authenticated API key | API key | 240/minute |

Limits use Django's cache. Production requires shared Redis; local-memory counters are development-only. Forwarded client addresses are ignored unless the direct proxy IP is explicitly trusted.

## Operational requirements and residual risks

- TLS certificates, HTTP-to-HTTPS redirection at the edge, proxy header stripping, firewall rules, database/Redis network isolation, encryption and rotation of backups, and log retention/alerting remain deployment responsibilities.
- Rate limiting reduces automated abuse but does not replace MFA, breached-password screening, bot detection, or an edge WAF. MFA should be the next authentication control for owners and platform administrators.
- Review logs for `login_failed`, `login_rate_limited`, `signup_rate_limited`, `invitation_rate_limited`, `csrf_rejected`, and repeated Django security warnings. Do not log passwords, raw invitation tokens, session IDs, API keys, or full request bodies.
- Rotate `SECRET_KEY` with short-lived `DJANGO_SECRET_KEY_FALLBACKS`; remove old keys after existing sessions and signed values have expired.
- Run `python manage.py check --deploy`, the complete test suite, dependency vulnerability scanning, and backup/restore verification for every production release.

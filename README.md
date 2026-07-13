# POS SaaS

Minimal Django + PostgreSQL POS SaaS scaffold.

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
python manage.py runserver
```

The local venv now uses PostgreSQL too, via `.env`, and the Dockerized database is exposed on `127.0.0.1:55432` to avoid colliding with the host PostgreSQL service already running on `5432` and the other local Docker databases already using `5433` and `5434`. If you want to point at a different local Postgres instance, update `DATABASE_URL` before running `migrate`.

## Docker setup

```powershell
docker compose up --build
```

Docker uses PostgreSQL through the `DATABASE_URL` passed in `docker-compose.yml` and publishes it on `55432` locally.

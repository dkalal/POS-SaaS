import os
from pathlib import Path

from django.core.management.base import CommandError
from django.db import connection


def postgres_command_context():
    if connection.vendor != "postgresql":
        raise CommandError("This command requires the PostgreSQL database backend.")
    config = connection.settings_dict
    args = []
    if config.get("HOST"):
        args.extend(["--host", str(config["HOST"])])
    if config.get("PORT"):
        args.extend(["--port", str(config["PORT"])])
    if config.get("USER"):
        args.extend(["--username", str(config["USER"])])
    env = os.environ.copy()
    if config.get("PASSWORD"):
        env["PGPASSWORD"] = str(config["PASSWORD"])
    options = config.get("OPTIONS", {})
    ssl_mapping = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }
    for option, variable in ssl_mapping.items():
        if options.get(option):
            env[variable] = str(options[option])
    return config, args, env


def require_regular_file(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise CommandError(f"Backup file does not exist: {path}")
    return path

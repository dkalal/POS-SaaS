import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from core.management.commands._postgres import postgres_command_context


class Command(BaseCommand):
    help = "Create a compressed, checksummed PostgreSQL custom-format backup."

    def add_arguments(self, parser):
        parser.add_argument("output", help="Destination .dump file")
        parser.add_argument("--overwrite", action="store_true", help="Replace an existing backup and manifest")

    def handle(self, *args, **options):
        config, connection_args, env = postgres_command_context()
        output = Path(options["output"]).expanduser().resolve()
        manifest = output.with_suffix(output.suffix + ".json")
        if not output.parent.is_dir():
            raise CommandError(f"Destination directory does not exist: {output.parent}")
        if not options["overwrite"] and (output.exists() or manifest.exists()):
            raise CommandError("Backup or manifest already exists; choose another path or pass --overwrite.")

        temporary = output.with_name(output.name + ".partial")
        if temporary.exists():
            raise CommandError(f"Stale temporary backup exists: {temporary}")
        connections.close_all()
        command = [
            "pg_dump", *connection_args, "--format=custom", "--compress=6",
            "--no-owner", "--no-acl", "--file", str(temporary), str(config["NAME"]),
        ]
        try:
            subprocess.run(command, env=env, check=True)
            digest = hashlib.sha256()
            with temporary.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            metadata = {
                "format": "postgresql-custom",
                "database": str(config["NAME"]),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest.hexdigest(),
                "size_bytes": temporary.stat().st_size,
            }
            os.replace(temporary, output)
            manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            try:
                output.chmod(0o600)
                manifest.chmod(0o600)
            except OSError:
                # Some Windows/network filesystems do not implement POSIX modes.
                pass
        except FileNotFoundError as exc:
            raise CommandError("pg_dump was not found; install a PostgreSQL client matching the server major version.") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError(f"pg_dump failed with exit code {exc.returncode}.") from exc
        finally:
            if temporary.exists():
                temporary.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup created: {output} ({metadata['sha256']})"))

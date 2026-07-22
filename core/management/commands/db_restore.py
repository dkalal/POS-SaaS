import hashlib
import json
import subprocess

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from core.management.commands._postgres import postgres_command_context, require_regular_file


class Command(BaseCommand):
    help = "Destructively restore a checksummed custom-format backup into the configured database."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="Source .dump file")
        parser.add_argument("--confirm-database", required=True, help="Must exactly match the configured database name")
        parser.add_argument("--yes", action="store_true", help="Acknowledge destructive replacement of database objects")
        parser.add_argument("--skip-checksum", action="store_true", help="Allow restore without a valid sidecar manifest")

    def handle(self, *args, **options):
        config, connection_args, env = postgres_command_context()
        backup = require_regular_file(options["backup"])
        database = str(config["NAME"])
        if not options["yes"] or options["confirm_database"] != database:
            raise CommandError("Restore refused: pass --yes and --confirm-database with the exact configured name.")
        if not options["skip_checksum"]:
            manifest_path = backup.with_suffix(backup.suffix + ".json")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommandError("A readable backup manifest is required unless --skip-checksum is passed.") from exc
            digest = hashlib.sha256()
            with backup.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != manifest.get("sha256"):
                raise CommandError("Backup checksum does not match its manifest; restore aborted.")

        connections.close_all()
        command = [
            "pg_restore", *connection_args, "--dbname", database, "--clean", "--if-exists",
            "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", str(backup),
        ]
        try:
            subprocess.run(command, env=env, check=True)
        except FileNotFoundError as exc:
            raise CommandError("pg_restore was not found; install a PostgreSQL client matching the backup version.") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError(f"pg_restore failed and rolled back (exit code {exc.returncode}).") from exc
        self.stdout.write(self.style.SUCCESS(f"Restore completed into database {database}. Run migrate and verification checks."))

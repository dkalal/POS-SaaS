from django.db import migrations, models


CREATE_POSTGRES_TRIGGER = """
CREATE OR REPLACE FUNCTION platform_audit_forbid_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.integrity_hash <> '' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'platform_admin_platformauditlog is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER platform_audit_append_only
BEFORE INSERT OR UPDATE OR DELETE ON platform_admin_platformauditlog
FOR EACH ROW EXECUTE FUNCTION platform_audit_forbid_mutation();
"""

DROP_POSTGRES_TRIGGER = """
DROP TRIGGER IF EXISTS platform_audit_append_only ON platform_admin_platformauditlog;
DROP FUNCTION IF EXISTS platform_audit_forbid_mutation();
"""


def create_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_POSTGRES_TRIGGER)


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_POSTGRES_TRIGGER)


class Migration(migrations.Migration):
    dependencies = [("platform_admin", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="platformauditlog",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name="platformauditlog",
            name="integrity_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="platformauditlog",
            name="previous_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddIndex(
            model_name="platformauditlog",
            index=models.Index(fields=["integrity_hash"], name="platform_audit_hash_idx"),
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]

from django.db import migrations, models


CREATE_POSTGRES_TRIGGER = """
CREATE OR REPLACE FUNCTION audit_auditevent_forbid_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.integrity_hash <> '' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_auditevent is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditevent_append_only
BEFORE INSERT OR UPDATE OR DELETE ON audit_auditevent
FOR EACH ROW EXECUTE FUNCTION audit_auditevent_forbid_mutation();
"""

DROP_POSTGRES_TRIGGER = """
DROP TRIGGER IF EXISTS audit_auditevent_append_only ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_auditevent_forbid_mutation();
"""


def create_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_POSTGRES_TRIGGER)


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_POSTGRES_TRIGGER)


class Migration(migrations.Migration):
    dependencies = [("audit", "0005_alter_auditevent_action")]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="integrity_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="previous_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["tenant", "integrity_hash"], name="audit_tenant_hash_idx"),
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]

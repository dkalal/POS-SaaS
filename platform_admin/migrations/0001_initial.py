from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("tenants", "0002_subscription_plans_and_tenant_lifecycle")]

    operations = [
        migrations.CreateModel(
            name="PlatformAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(choices=[("tenant_created", "Tenant created"), ("tenant_activated", "Tenant activated"), ("tenant_suspended", "Tenant suspended"), ("tenant_cancelled", "Tenant cancelled"), ("trial_extended", "Trial extended"), ("plan_changed", "Plan changed"), ("plan_created", "Plan created"), ("plan_updated", "Plan updated"), ("plan_disabled", "Plan disabled")], max_length=32)),
                ("before_data", models.JSONField(blank=True, default=dict)),
                ("after_data", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="platform_audit_logs", to=settings.AUTH_USER_MODEL)),
                ("target_tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="platform_audit_logs", to="tenants.tenant")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="platformauditlog", index=models.Index(fields=["target_tenant", "action"], name="platform_ad_target__928028_idx")),
        migrations.AddIndex(model_name="platformauditlog", index=models.Index(fields=["created_at"], name="platform_ad_created_52c202_idx")),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0004_tenant_default_reorder_level_tenant_receipt_footer_and_more")]

    operations = [
        migrations.AddField(
            model_name="onboardingprogress",
            name="dismissed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

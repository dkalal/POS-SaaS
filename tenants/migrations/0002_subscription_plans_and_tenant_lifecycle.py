# Generated to preserve the existing Tenant primary key and all customer data.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tenants", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                ("code", models.SlugField(max_length=64, unique=True)),
                ("monthly_price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("annual_price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("trial_days", models.PositiveIntegerField(default=14)),
                ("max_users", models.PositiveIntegerField(default=5)),
                ("feature_limits", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["monthly_price", "name"]},
        ),
        migrations.CreateModel(
            name="TenantSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("trial", "Trial"), ("active", "Active"), ("cancelled", "Cancelled")], default="trial", max_length=16)),
                ("billing_cycle", models.CharField(choices=[("monthly", "Monthly"), ("annual", "Annual")], default="monthly", max_length=16)),
                ("started_at", models.DateTimeField()),
                ("current_period_ends_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("internal_notes", models.TextField(blank=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="tenants.subscriptionplan")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="tenants.tenant")),
            ],
            options={"ordering": ["-started_at", "-id"]},
        ),
        migrations.AddIndex(model_name="tenantsubscription", index=models.Index(fields=["tenant", "status"], name="tenants_ten_tenant__679851_idx")),
        migrations.AddField(model_name="tenant", name="address", field=models.TextField(blank=True)),
        migrations.AddField(model_name="tenant", name="business_type", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="tenant", name="contact_email", field=models.EmailField(blank=True, max_length=254)),
        migrations.AddField(model_name="tenant", name="contact_name", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="tenant", name="contact_phone", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="tenant", name="country", field=models.CharField(default="TZ", max_length=2)),
        migrations.AddField(model_name="tenant", name="currency", field=models.CharField(default="TZS", max_length=3)),
        migrations.AddField(model_name="tenant", name="status", field=models.CharField(choices=[("trial", "Trial"), ("active", "Active"), ("suspended", "Suspended"), ("cancelled", "Cancelled")], default="trial", max_length=16)),
        migrations.AddField(model_name="tenant", name="subscription_plan", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="current_tenants", to="tenants.subscriptionplan")),
        migrations.AddField(model_name="tenant", name="timezone", field=models.CharField(default="Africa/Dar_es_Salaam", max_length=64)),
        migrations.AddField(model_name="tenant", name="trial_ends_at", field=models.DateTimeField(blank=True, null=True)),
    ]

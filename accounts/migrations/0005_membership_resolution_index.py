from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_remove_tenantinvitation_accounts_te_token_c89c80_idx_and_more")]
    operations = [
        migrations.AddIndex(
            model_name="tenantmembership",
            index=models.Index(
                fields=["user", "status", "is_active", "tenant"],
                name="accounts_mem_resolve_idx",
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0001_initial")]
    operations = [
        migrations.AddIndex(
            model_name="purchase",
            index=models.Index(fields=["tenant", "status", "received_date"], name="purchase_tenant_recv_idx"),
        ),
    ]

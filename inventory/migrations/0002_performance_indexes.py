from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["tenant", "product", "created_at"], name="stock_tenant_product_time_idx"),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sales", "0002_invoice_invoiceitem_quotation_quotationitem_and_more")]
    operations = [
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["tenant", "status", "created_at"], name="sales_tenant_status_time_idx"),
        ),
        migrations.AddIndex(
            model_name="receipt",
            index=models.Index(fields=["tenant", "issued_at"], name="sales_tenant_receipt_time_idx"),
        ),
    ]

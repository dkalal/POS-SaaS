import re
import unicodedata

from django.db import migrations, models


def _ascii_words(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[A-Za-z0-9]+", value.upper())


def _category_code(category):
    words = _ascii_words(getattr(category, "name", ""))
    if not words:
        return "GEN"
    if len(words) > 1:
        return "".join(word[0] for word in words)[:3].ljust(3, "X")
    if words[0].startswith("SERVICE"):
        return "SRV"
    return words[0][:3].ljust(3, "X")


def _name_code(name):
    parts = []
    for token in _ascii_words(name):
        if token.isdigit() and parts:
            parts[-1] += token
        else:
            parts.append(token)
    return ("-".join(parts) or "PRODUCT")[:54].rstrip("-")


def backfill_blank_skus(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for product in Product.objects.filter(models.Q(sku="") | models.Q(sku__isnull=True)).select_related("category").order_by("tenant_id", "id"):
        prefix = f"{_category_code(product.category)}-{_name_code(product.name)}"
        sequence = 1
        while Product.objects.filter(tenant_id=product.tenant_id, sku=f"{prefix}-{sequence:03d}").exists():
            sequence += 1
        product.sku = f"{prefix}-{sequence:03d}"
        product.save(update_fields=["sku"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_blank_skus, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterUniqueTogether(
            name="product",
            unique_together={("tenant", "barcode")},
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(fields=("tenant", "sku"), name="unique_product_sku_per_tenant"),
        ),
    ]

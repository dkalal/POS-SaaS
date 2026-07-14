import re
import unicodedata

from catalog.models import Product


def normalize_sku(value):
    """Return the standard, human-readable representation of a manual SKU."""
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-")
    return value


def _category_code(category):
    category_name = getattr(category, "name", "") or "GENERAL"
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", category_name).upper())
    if not words:
        return "GEN"
    if len(words) > 1:
        return "".join(word[0] for word in words)[:3].ljust(3, "X")

    word = words[0]
    # "Services" is a common POS category where SRV is clearer than SER.
    if word.startswith("SERVICE"):
        return "SRV"
    return word[:3].ljust(3, "X")


def _product_name_code(name):
    normalized = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[A-Za-z0-9]+", normalized.upper())
    if not tokens:
        return "PRODUCT"

    parts = []
    for token in tokens:
        if token.isdigit() and parts:
            parts[-1] += token
        else:
            parts.append(token)
    return "-".join(parts)[:54].rstrip("-")


def sku_prefix(*, category, name):
    return f"{_category_code(category)}-{_product_name_code(name)}"


def generate_product_sku(*, tenant, category, name, exclude_product_id=None):
    """Generate the next SKU for one business and one readable product prefix."""
    prefix = sku_prefix(category=category, name=name)
    queryset = Product.objects.filter(tenant=tenant, sku__startswith=f"{prefix}-")
    if exclude_product_id is not None:
        queryset = queryset.exclude(pk=exclude_product_id)

    sequence = 0
    for existing_sku in queryset.values_list("sku", flat=True):
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", existing_sku or "")
        if match:
            sequence = max(sequence, int(match.group(1)))
    return f"{prefix}-{sequence + 1:03d}"

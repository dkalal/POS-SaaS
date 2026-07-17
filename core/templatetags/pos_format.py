from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def money(context, value, currency=None):
    """Render an amount consistently using the active workspace currency."""
    if value in (None, ""):
        value = Decimal("0")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    currency = (currency or getattr(getattr(context.get("request"), "tenant", None), "currency", None) or "TZS").upper()
    return f"{currency} {amount:,.2f}"

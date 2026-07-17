from decimal import Decimal, InvalidOperation


def format_money(value, currency="TZS"):
    if value in (None, ""):
        value = Decimal("0")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    return f"{(currency or 'TZS').upper()} {amount:,.2f}"

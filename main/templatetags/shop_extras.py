from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def uz_price(value):
    """
    Format monetary amount with space thousand separators and ' so'm':
    36068578000 -> 36 068 578 000 so'm
    1000200 -> 1 000 200 so'm
    """
    if value is None or value == '':
        return "0 so'm"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "0 so'm"
    formatted = f"{amount:,.0f}".replace(',', ' ')
    return f"{formatted} so'm"


@register.filter
def intspace(value):
    """
    Format any integer/float/Decimal with space thousand separators:
    36068578000 -> 36 068 578 000
    57271 -> 57 271
    1000200 -> 1 000 200
    """
    if value is None or value == '':
        return "0"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    return f"{amount:,.0f}".replace(',', ' ')


@register.filter
def compact_money(value):
    """
    Format large monetary amounts to human-readable short form in Uzbek:
    36068578000 -> 36.1 mlrd so'm
    1200000 -> 1.2 mln so'm
    850000 -> 850 ming so'm
    """
    if value is None or value == '':
        return "0 so'm"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "0 so'm"

    if val >= 1_000_000_000:
        res = f"{val / 1_000_000_000:.2f}".rstrip('0').rstrip('.')
        return f"{res} mlrd so'm"
    elif val >= 1_000_000:
        res = f"{val / 1_000_000:.1f}".rstrip('0').rstrip('.')
        return f"{res} mln so'm"
    elif val >= 100_000:
        res = f"{val / 1_000:.0f}"
        return f"{res} ming so'm"
    else:
        return f"{val:,.0f} so'm".replace(',', ' ')


@register.filter
def monthly_price(value, months=12):
    if value is None or value == '':
        return "0 so'm/oyiga"
    try:
        amount = Decimal(str(value)) / Decimal(months)
    except (InvalidOperation, TypeError, ValueError):
        return "0 so'm/oyiga"
    formatted = f"{amount:,.0f}".replace(',', ' ')
    return f"{formatted} so'm/oyiga"

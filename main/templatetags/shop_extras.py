from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def uz_price(value):
    if value is None or value == '':
        return "0 so'm"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "0 so'm"
    formatted = f"{amount:,.0f}".replace(',', ' ')
    return f"{formatted} so'm"


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


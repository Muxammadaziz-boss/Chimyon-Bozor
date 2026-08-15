from decimal import Decimal, InvalidOperation
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def uz_price(value):
    """
    Format monetary amount with space thousand separators and ' so'm':
    36068578000 -> 36 068 578 000 so'm
    1000200 -> 1 000 200 so'm
    """
    if value is None or value == '':
        return mark_safe("0 so'm")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return mark_safe("0 so'm")
    formatted = f"{amount:,.0f}".replace(',', ' ')
    return mark_safe(f"{formatted} so'm")



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
        return mark_safe("0 so'm")
    try:
        val = float(value)
    except (TypeError, ValueError):
        return mark_safe("0 so'm")

    if val >= 1_000_000_000:
        res = f"{val / 1_000_000_000:.2f}".rstrip('0').rstrip('.')
        return mark_safe(f"{res} mlrd so'm")
    elif val >= 1_000_000:
        res = f"{val / 1_000_000:.1f}".rstrip('0').rstrip('.')
        return mark_safe(f"{res} mln so'm")
    elif val >= 100_000:
        res = f"{val / 1_000:.0f}"
        return mark_safe(f"{res} ming so'm")
    else:
        return mark_safe(f"{val:,.0f} so'm".replace(',', ' '))


@register.filter
def monthly_price(value, months=12):
    if value is None or value == '':
        return mark_safe("0 so'm/oyiga")
    try:
        amount = Decimal(str(value)) / Decimal(months)
    except (InvalidOperation, TypeError, ValueError):
        return mark_safe("0 so'm/oyiga")
    formatted = f"{amount:,.0f}".replace(',', ' ')
    return mark_safe(f"{formatted} so'm/oyiga")


@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """
    Returns updated querystring with specified parameters modified/removed.
    Usage: {% query_transform page=1 sort='price_asc' %}
    To remove a parameter, pass param=None or param=''
    """
    request = context.get('request')
    if not request:
        return ''
    query_dict = request.GET.copy()
    for k, v in kwargs.items():
        if v is None or v == '':
            query_dict.pop(k, None)
        else:
            query_dict[k] = str(v)
    encoded = query_dict.urlencode()
    return f"?{encoded}" if encoded else "?"



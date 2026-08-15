from django.db.models import Count, Q
from .models import SiteSettings, Category


def site_settings(request):
    settings_obj, _ = SiteSettings.objects.get_or_create(pk=1)
    
    # Query only top 9 active categories for the header dropdown to prevent viewport clutter
    nav_categories = (
        Category.objects.filter(is_active=True)
        .annotate(active_products_count=Count('product', filter=Q(product__count__gte=0)))
        .order_by('-active_products_count', 'name')[:9]
    )
    
    total_categories_count = Category.objects.filter(is_active=True).count()

    return {
        'site_settings': settings_obj,
        'nav_categories': nav_categories,
        'total_categories_count': total_categories_count,
    }


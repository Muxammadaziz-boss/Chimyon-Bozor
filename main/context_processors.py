from .models import SiteSettings, Category


def site_settings(request):
    settings_obj, _ = SiteSettings.objects.get_or_create(pk=1)
    categories = Category.objects.filter(is_active=True)
    return {
        'site_settings': settings_obj,
        'nav_categories': categories,
    }

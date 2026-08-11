from .models import SiteSettings


def site_settings(request):
    settings_obj, _ = SiteSettings.objects.get_or_create(pk=1)
    return {'site_settings': settings_obj}

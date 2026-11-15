from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_gdal_available(app_configs, **kwargs):
    if getattr(settings, "GDAL_AVAILABLE", False):
        return []
    return [
        Warning(
            "GDAL is not installed, so GeoDjango spatial fields are disabled. "
            "PostGIS is still verified with SQL when the database provides the extension.",
            id="glitters.W001",
        )
    ]

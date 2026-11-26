from config.settings.development import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "apps.construction.apps.ConstructionConfig"]  # noqa: F405
DATABASES["default"]["TEST"] = {"NAME": "construction_erp_test"}  # noqa: F405

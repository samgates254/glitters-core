from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"
    label = "identity"
    verbose_name = "Identity"

    def ready(self) -> None:
        from django.contrib import admin

        admin.site.site_header = "GLITTERS CORE"
        admin.site.site_title = "GLITTERS CORE"
        admin.site.index_title = "Platform operations"

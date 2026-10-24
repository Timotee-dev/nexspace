from django.apps import AppConfig


class MobileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mobile"
    label = "mobile"
    verbose_name = "Mobile app"

    def ready(self):
        from . import schema, signals  # noqa: F401

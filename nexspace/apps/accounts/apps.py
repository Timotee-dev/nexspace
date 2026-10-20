from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401
        from .owner import ensure_all_owners

        post_migrate.connect(ensure_all_owners, sender=self)

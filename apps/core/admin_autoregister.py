"""Register every model not already in the Django admin, so platform admins can view and edit
every table at /django-admin/ ("database access"). Hand-written ModelAdmins always take priority."""
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered


def register_remaining():
    for model in apps.get_models():
        if model._meta.app_label not in {"sessions", "contenttypes"} and not admin.site.is_registered(model):
            fields = [f.name for f in model._meta.concrete_fields if f.get_internal_type() not in ("TextField", "JSONField")]
            options = {"list_display": fields[:6], "list_per_page": 50}
            fk = [f.name for f in model._meta.concrete_fields if f.is_relation]
            if fk:
                options["raw_id_fields"] = fk
            try:
                admin.site.register(model, type(f"{model.__name__}AutoAdmin", (admin.ModelAdmin,), options))
            except AlreadyRegistered:
                pass

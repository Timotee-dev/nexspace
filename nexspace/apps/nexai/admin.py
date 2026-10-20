from django.contrib import admin

from .models import Usage


@admin.register(Usage)
class UsageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "mode", "used_model", "input_tokens", "output_tokens")
    list_filter = ("mode", "used_model")

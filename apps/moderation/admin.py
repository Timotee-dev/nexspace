from django.contrib import admin

from .models import ModerationAction, Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "target_type", "target_id", "reason", "status", "reporter")
    list_filter = ("status", "reason", "target_type")


@admin.register(ModerationAction)
class ModerationActionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target_type", "target_label", "moderator")
    list_filter = ("action",)
    readonly_fields = [f.name for f in ModerationAction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

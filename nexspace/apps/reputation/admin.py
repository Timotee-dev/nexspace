from django.contrib import admin

from .models import NexScoreEvent


@admin.register(NexScoreEvent)
class NexScoreEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "amount", "reason", "actor", "is_reversal")
    list_filter = ("reason", "is_reversal")
    search_fields = ("user__username",)
    readonly_fields = [f.name for f in NexScoreEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

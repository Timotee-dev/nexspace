from django.contrib import admin

from .models import AcademicEvent, Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "audience", "department", "expires_at", "is_removed", "created_at")
    list_filter = ("priority", "audience", "is_removed")


@admin.register(AcademicEvent)
class AcademicEventAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "starts_at", "audience", "department")
    list_filter = ("kind", "audience")

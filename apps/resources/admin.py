from django.contrib import admin

from .models import Resource, ResourceRating


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "resource_type", "uploaded_by", "is_verified_upload", "download_count", "is_removed")
    list_filter = ("resource_type", "is_verified_upload", "is_removed", "course__department")
    search_fields = ("title", "course__code")
    raw_id_fields = ("uploaded_by",)


admin.site.register(ResourceRating)

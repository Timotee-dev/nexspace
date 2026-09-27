from django.contrib import admin

from .models import Space, SpaceMembership


class MembershipInline(admin.TabularInline):
    model = SpaceMembership
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "department", "is_official", "member_count")
    list_filter = ("kind", "is_official", "department")
    search_fields = ("name", "slug")
    inlines = [MembershipInline]

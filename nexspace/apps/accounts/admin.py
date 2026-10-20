from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Profile, RoleAssignment, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    filter_horizontal = ("interests",)


class RoleInline(admin.TabularInline):
    model = RoleAssignment
    fk_name = "user"
    extra = 0
    readonly_fields = ("granted_by", "created_at")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "username", "department", "level", "email_verified", "is_active")
    list_filter = ("email_verified", "is_active", "is_staff", "department", "level")
    search_fields = ("email", "full_name", "username", "matric_number")
    inlines = [ProfileInline, RoleInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identity", {"fields": ("full_name", "username", "department", "level", "matric_number")}),
        ("Status", {"fields": ("email_verified", "email_verified_at", "onboarding_completed", "is_active")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2")}),
    )


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "department", "granted_by", "created_at")
    list_filter = ("role", "department")
    search_fields = ("user__email", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("granted_by",)

    def save_model(self, request, obj, form, change):
        from apps.core.models import audit

        if not change:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)
        audit(request.user, "role.assigned" if not change else "role.changed", obj.user,
              role=obj.role, department_id=obj.department_id)

    def delete_model(self, request, obj):
        from apps.core.models import audit

        audit(request.user, "role.revoked", obj.user, role=obj.role, department_id=obj.department_id)
        super().delete_model(request, obj)

from django.contrib import admin

from .models import AcademicSession, Course, Department, Faculty, University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name")
    prepopulated_fields = {"slug": ("short_name",)}


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ("name", "university")
    list_filter = ("university",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "faculty", "is_active")
    list_filter = ("faculty__university", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "university", "is_current")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "department", "level", "semester", "units", "is_active")
    list_filter = ("department", "level", "semester", "is_active")
    search_fields = ("code", "title")
    readonly_fields = ("slug",)

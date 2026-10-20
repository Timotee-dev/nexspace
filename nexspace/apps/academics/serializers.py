from rest_framework import serializers

from .models import Department


class DepartmentSerializer(serializers.ModelSerializer):
    faculty = serializers.CharField(source="faculty.name")
    university = serializers.CharField(source="faculty.university.short_name")

    class Meta:
        model = Department
        fields = ["id", "name", "code", "slug", "faculty", "university"]

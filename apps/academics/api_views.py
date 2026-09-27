from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from .models import Department, Level
from .serializers import DepartmentSerializer


class DepartmentListView(generics.ListAPIView):
    """Active departments, used by the sign-up form. Public."""

    permission_classes = [AllowAny]
    serializer_class = DepartmentSerializer
    pagination_class = None
    queryset = Department.objects.filter(is_active=True).select_related("faculty__university")


class LevelSerializer(serializers.Serializer):
    value = serializers.IntegerField()
    label = serializers.CharField()


class LevelListView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=LevelSerializer(many=True))
    def get(self, request):
        return Response([{"value": value, "label": label} for value, label in Level.choices])

from django.urls import path
from rest_framework import generics, serializers
from rest_framework.permissions import AllowAny

from .models import Topic


class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ["id", "name", "slug", "description"]


class TopicListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = TopicSerializer
    pagination_class = None
    queryset = Topic.objects.filter(is_active=True)


urlpatterns = [path("", TopicListView.as_view(), name="api-topics")]

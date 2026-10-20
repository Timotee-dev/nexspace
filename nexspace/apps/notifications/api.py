from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Category, Notification, PushSubscription


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "category", "kind", "text", "url", "is_read", "is_critical", "created_at"]


class NotificationListAPIView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.none()  # real queryset below; this keeps schema generation happy

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user)
        category = self.request.query_params.get("category")
        return qs.filter(category=category) if category in Category.values else qs


class UnreadCountAPIView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response({"unread": services.unread_count(request.user)})


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class MarkReadAPIView(APIView):
    @extend_schema(request=MarkReadSerializer, responses={200: dict})
    def post(self, request):
        s = MarkReadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        updated = services.mark_read(request.user, s.validated_data.get("ids"))
        return Response({"updated": updated, "unread": services.unread_count(request.user)})


class PushSubscriptionSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=600)
    keys = serializers.DictField(child=serializers.CharField(max_length=200))

    def validate_endpoint(self, value):
        if not value.startswith("https://"):
            raise serializers.ValidationError("Push endpoints must use HTTPS.")
        return value

    def validate_keys(self, value):
        if not value.get("p256dh") or not value.get("auth"):
            raise serializers.ValidationError("Missing p256dh or auth key.")
        return value


class PushSubscriptionAPIView(APIView):
    @extend_schema(request=PushSubscriptionSerializer, responses={201: dict})
    def post(self, request):
        s = PushSubscriptionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        PushSubscription.objects.update_or_create(
            endpoint=d["endpoint"],
            defaults={"user": request.user, "p256dh": d["keys"]["p256dh"], "auth": d["keys"]["auth"],
                      "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200]},
        )
        return Response({"subscribed": True}, status=status.HTTP_201_CREATED)

    @extend_schema(request=PushSubscriptionSerializer, responses={204: None})
    def delete(self, request):
        endpoint = request.data.get("endpoint", "")
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



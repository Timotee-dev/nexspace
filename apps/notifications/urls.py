from django.urls import path

from . import api, views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.list_view, name="list"),
    path("notifications/<int:pk>/open/", views.open_view, name="open"),
    path("notifications/read-all/", views.mark_all_view, name="mark-all"),
    path("settings/notifications/", views.preferences_view, name="preferences"),
]

api_urlpatterns = [
    path("notifications/", api.NotificationListAPIView.as_view(), name="api-notifications"),
    path("notifications/unread-count/", api.UnreadCountAPIView.as_view(), name="api-unread-count"),
    path("notifications/mark-read/", api.MarkReadAPIView.as_view(), name="api-mark-read"),
    path("push/subscriptions/", api.PushSubscriptionAPIView.as_view(), name="api-push-subscriptions"),
]

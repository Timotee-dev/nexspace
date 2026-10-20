from django.urls import path

from . import views

app_name = "moderation"

urlpatterns = [
    path("report/<str:target_type>/<int:target_id>/", views.report_view, name="report"),
    path("moderation/", views.queue_view, name="queue"),
    path("moderation/action/", views.action_view, name="action"),
    path("moderation/reveal/<int:pk>/", views.reveal_view, name="reveal"),
    path("moderation/log/", views.log_view, name="log"),
]

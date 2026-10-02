from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("messages/", views.inbox_view, name="inbox"),
    path("messages/new/<str:username>/", views.start_view, name="start"),
    path("messages/block/<str:username>/", views.block_user_view, name="block"),
    path("messages/<int:pk>/", views.conversation_view, name="conversation"),
    path("messages/<int:pk>/send/", views.send_view, name="send"),
    path("messages/<int:pk>/poll/", views.poll_view, name="poll"),
    path("messages/<int:pk>/action/", views.conversation_action_view, name="action"),
    path("messages/<int:pk>/delete/<int:message_id>/", views.delete_message_view, name="delete"),
    path("live/", views.live_view, name="live"),
]

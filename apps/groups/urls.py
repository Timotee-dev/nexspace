from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("groups/", views.list_view, name="list"),
    path("groups/new/", views.create_view, name="create"),
    path("groups/<int:pk>/", views.detail_view, name="detail"),
    path("groups/<int:pk>/membership/", views.membership_view, name="membership"),
    path("groups/<int:pk>/messages/", views.message_view, name="message"),
    path("groups/<int:pk>/meeting/", views.meeting_view, name="meeting"),
]

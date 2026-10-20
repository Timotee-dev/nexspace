from django.urls import path

from . import views

app_name = "social"

urlpatterns = [
    path("u/<str:username>/follow/", views.follow_user_view, name="follow-user"),
    path("t/<slug:slug>/follow/", views.follow_topic_view, name="follow-topic"),
]

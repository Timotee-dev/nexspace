from django.urls import path

from . import views

app_name = "discover"

urlpatterns = [
    path("explore/", views.explore_view, name="explore"),
    path("opportunities/", views.opportunities_view, name="opportunities"),
    path("opportunities/<int:pk>/remind/", views.reminder_view, name="remind"),
]

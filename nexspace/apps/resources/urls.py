from django.urls import path

from . import views

app_name = "resources"

urlpatterns = [
    path("resources/", views.library_view, name="library"),
    path("resources/upload/", views.upload_view, name="upload"),
    path("resources/<int:pk>/", views.detail_view, name="detail"),
    path("resources/<int:pk>/download/", views.download_view, name="download"),
    path("resources/<int:pk>/rate/", views.rate_view, name="rate"),
    path("resources/<int:pk>/remove/", views.remove_view, name="remove"),
]

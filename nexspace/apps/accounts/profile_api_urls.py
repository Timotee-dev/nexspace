from django.urls import path

from . import api_views

urlpatterns = [
    path("<str:username>/", api_views.PublicProfileAPIView.as_view(), name="api-profile"),
]

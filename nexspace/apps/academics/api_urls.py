from django.urls import path

from . import api_views

urlpatterns = [
    path("departments/", api_views.DepartmentListView.as_view(), name="api-departments"),
    path("levels/", api_views.LevelListView.as_view(), name="api-levels"),
]

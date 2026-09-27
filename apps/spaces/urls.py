from django.urls import path

from . import views

app_name = "spaces"

urlpatterns = [
    path("spaces/", views.space_list_view, name="list"),
    path("spaces/new/", views.space_create_view, name="create"),
    path("spaces/<int:pk>/membership/", views.membership_view, name="membership"),
    path("s/<slug:slug>/", views.space_detail_view, name="detail"),
    path("courses/", views.course_list_view, name="courses"),
    path("courses/<slug:slug>/", views.course_detail_view, name="course"),
]

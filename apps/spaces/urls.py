from django.urls import path

from . import views

app_name = "spaces"

urlpatterns = [
    path("spaces/", views.space_list_view, name="list"),
    path("spaces/new/", views.space_create_view, name="create"),
    path("spaces/<int:pk>/membership/", views.membership_view, name="membership"),
    path("spaces/<int:pk>/requests/<int:request_id>/", views.request_decision_view, name="request-decision"),
    path("spaces/<int:pk>/members/<int:user_id>/remove/", views.remove_member_view, name="remove-member"),
    path("s/<slug:slug>/", views.space_detail_view, name="detail"),
    path("courses/", views.course_list_view, name="courses"),
    path("courses/<slug:slug>/", views.course_detail_view, name="course"),
]

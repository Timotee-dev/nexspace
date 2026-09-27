from django.urls import path

from . import views

app_name = "manage"

urlpatterns = [
    path("manage/", views.overview_view, name="overview"),
    path("manage/users/", views.users_view, name="users"),
    path("manage/users/<int:pk>/", views.user_detail_view, name="user"),
    path("manage/users/<int:pk>/action/", views.user_action_view, name="user-action"),
    path("manage/courses/", views.courses_view, name="courses"),
    path("manage/courses/new/", views.course_form_view, name="course-create"),
    path("manage/courses/<int:pk>/", views.course_form_view, name="course-edit"),
    path("manage/sessions/", views.sessions_view, name="sessions"),
    path("manage/sessions/<int:pk>/current/", views.session_current_view, name="session-current"),
    path("manage/spaces/", views.spaces_view, name="spaces"),
    path("manage/spaces/new/", views.space_form_view, name="space-create"),
    path("manage/spaces/<int:pk>/", views.space_form_view, name="space-edit"),
    path("manage/spaces/<int:pk>/delete/", views.space_delete_view, name="space-delete"),
    path("manage/announcements/", views.announcements_view, name="announcements"),
    path("manage/announcements/<int:pk>/remove/", views.announcement_remove_view, name="announcement-remove"),
    path("manage/departments/", views.departments_view, name="departments"),
    path("manage/topics/", views.topics_view, name="topics"),
    path("manage/topics/<int:pk>/toggle/", views.topic_toggle_view, name="topic-toggle"),
]

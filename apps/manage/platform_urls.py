from django.urls import path

from . import platform as views

app_name = "platform"

urlpatterns = [
    path("platform/", views.overview_view, name="overview"),
    path("platform/run-jobs/", views.run_jobs_view, name="run-jobs"),
    path("platform/activity/", views.activity_view, name="activity"),
    path("platform/people/", views.people_view, name="people"),
    path("platform/staff/", views.staff_view, name="staff"),
    path("platform/staff/<int:pk>/", views.staff_decision_view, name="staff-decision"),
    path("platform/audit/", views.audit_view, name="audit"),
    path("platform/database/", views.database_view, name="database"),
]

from django.urls import path

from . import views

app_name = "notices"

urlpatterns = [
    path("announcements/", views.announcements_view, name="announcements"),
    path("announcements/new/", views.announcement_create_view, name="announcement-create"),
    path("announcements/<int:pk>/", views.announcement_detail_view, name="announcement"),
    path("announcements/<int:pk>/attachment/", views.announcement_attachment_view, name="announcement-attachment"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/new/", views.event_create_view, name="event-create"),
]

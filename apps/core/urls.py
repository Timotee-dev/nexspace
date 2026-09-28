from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("internal/run-scheduled/", views.run_scheduled_view, name="run-scheduled"),
    path("privacy/", TemplateView.as_view(template_name="core/privacy.html"), name="privacy"),
    path("guidelines/", TemplateView.as_view(template_name="core/guidelines.html"), name="guidelines"),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="core/robots.txt", content_type="text/plain"),
        name="robots",
    ),
]

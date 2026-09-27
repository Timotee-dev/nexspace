from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home_view, name="home"),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="core/robots.txt", content_type="text/plain"),
        name="robots",
    ),
]

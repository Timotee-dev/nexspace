from django.urls import path

from . import views

app_name = "nexai"

urlpatterns = [
    path("nexai/", views.ask_view, name="ask"),
    path("nexai/resources/<int:pk>/summary/", views.summary_view, name="summary"),
    path("nexai/resources/<int:pk>/quiz/", views.quiz_view, name="quiz"),
    path("nexai/courses/<slug:slug>/insights/", views.insights_view, name="insights"),
]

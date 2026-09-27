from django.urls import path

from . import api_views

urlpatterns = [
    path("signup/", api_views.SignupAPIView.as_view(), name="api-signup"),
    path("login/", api_views.LoginAPIView.as_view(), name="api-login"),
    path("logout/", api_views.LogoutAPIView.as_view(), name="api-logout"),
    path("me/", api_views.MeAPIView.as_view(), name="api-me"),
    path("resend-verification/", api_views.ResendVerificationAPIView.as_view(), name="api-resend-verification"),
]

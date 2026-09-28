from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("verify-email/resend/", views.resend_verification_view, name="resend-verification"),
    path("verify-email/<str:token>/", views.verify_email_view, name="verify-email"),
    path("onboarding/", views.onboarding_view, name="onboarding"),
    path("u/<str:username>/", views.profile_detail_view, name="profile"),
    path("settings/", views.settings_profile_view, name="settings-profile"),
    path("settings/privacy/", views.settings_privacy_view, name="settings-privacy"),
    path("settings/account/", views.settings_account_view, name="settings-account"),
    path("settings/delete-account/", views.delete_account_view, name="delete-account"),
    # Password reset (Django's secure token flow with NexSpace templates)
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            email_template_name="emails/password_reset.txt",
            html_email_template_name="emails/password_reset.html",
            subject_template_name="emails/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password-reset-sent"),
        ),
        name="password-reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_sent.html"),
        name="password-reset-sent",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password-reset-complete"),
        ),
        name="password-reset-confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password-reset-complete",
    ),
]

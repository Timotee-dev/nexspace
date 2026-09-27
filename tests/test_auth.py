import re

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import make_verification_token

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


def signup_data(department, **overrides):
    data = {
        "full_name": "Ada Lovelace",
        "email": "Ada@Example.com",
        "department": department.pk,
        "level": 200,
        "matric_number": "",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    data.update(overrides)
    return data


def test_signup_creates_user_profile_and_sends_verification(client, department):
    response = client.post(reverse("accounts:signup"), signup_data(department))
    assert response.status_code == 302
    assert response.url == reverse("accounts:onboarding")
    user = User.objects.get(email="ada@example.com")
    assert user.profile is not None
    assert user.username == "ada_lovelace"
    assert user.email_verified is False
    assert user.check_password(PASSWORD)
    assert len(mail.outbox) == 1
    assert "/verify-email/" in mail.outbox[0].body


def test_signup_rejects_mismatched_and_weak_passwords(client, department):
    r = client.post(reverse("accounts:signup"), signup_data(department, confirm_password="different"))
    assert r.status_code == 200 and not User.objects.exists()
    r = client.post(reverse("accounts:signup"), signup_data(department, password="12345678", confirm_password="12345678"))
    assert r.status_code == 200 and not User.objects.exists()


def test_signup_rejects_duplicate_email_case_insensitive(client, department, user):
    r = client.post(reverse("accounts:signup"), signup_data(department, email="STUDENT@example.com"))
    assert r.status_code == 200
    assert User.objects.count() == 1


def test_blank_matric_numbers_never_collide(client, department):
    """Regression: blank matric numbers must be stored as NULL, not ''."""
    for i in range(3):
        client.post(reverse("accounts:signup"), signup_data(department, email=f"s{i}@example.com"))
        client.logout()
    assert User.objects.count() == 3
    assert User.objects.filter(matric_number__isnull=True).count() == 3


def test_duplicate_matric_number_rejected(client, department, make_user):
    make_user(email="first@example.com", matric_number="csc/2021/001")
    r = client.post(reverse("accounts:signup"), signup_data(department, matric_number="CSC/2021/001"))
    assert r.status_code == 200
    assert "already linked" in r.content.decode()


def test_usernames_are_unique(department):
    a = User.objects.create_user(email="a@x.com", password=PASSWORD, full_name="Same Name", department=department, level=100)
    b = User.objects.create_user(email="b@x.com", password=PASSWORD, full_name="Same Name", department=department, level=100)
    assert a.username != b.username


def test_email_verification_link(client, make_user):
    user = make_user(verified=False)
    token = make_verification_token(user)
    r = client.get(reverse("accounts:verify-email", args=[token]))
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.email_verified and user.email_verified_at


def test_verification_link_invalid_or_stale(client, make_user):
    user = make_user(verified=False)
    token = make_verification_token(user)
    assert client.get(reverse("accounts:verify-email", args=["garbage"])).status_code == 400
    user.email = "changed@example.com"
    user.save()
    assert client.get(reverse("accounts:verify-email", args=[token])).status_code == 400
    user.refresh_from_db()
    assert not user.email_verified


def test_resend_verification_has_cooldown(client, make_user):
    user = make_user(verified=False)
    client.force_login(user)
    client.post(reverse("accounts:resend-verification"))
    client.post(reverse("accounts:resend-verification"))
    assert len(mail.outbox) == 1


def test_login_and_logout(client, user):
    r = client.post(reverse("accounts:login"), {"email": "STUDENT@example.com", "password": PASSWORD})
    assert r.status_code == 302
    assert client.get(reverse("core:home")).status_code == 200
    client.post(reverse("accounts:logout"))
    r = client.get(reverse("accounts:settings-profile"))
    assert r.status_code == 302 and reverse("accounts:login") in r.url


def test_logout_requires_post(client, user):
    client.force_login(user)
    assert client.get(reverse("accounts:logout")).status_code == 405


def test_login_lockout_after_repeated_failures(client, user):
    url = reverse("accounts:login")
    for _ in range(5):
        client.post(url, {"email": user.email, "password": "wrong"})
    r = client.post(url, {"email": user.email, "password": PASSWORD})
    assert r.status_code == 429
    assert "_auth_user_id" not in client.session


def test_login_rejects_unsafe_next_redirect(client, user):
    r = client.post(reverse("accounts:login") + "?next=https://evil.example.com",
                    {"email": user.email, "password": PASSWORD, "next": "https://evil.example.com"})
    assert r.status_code == 302 and r.url == reverse("core:home")


def test_password_reset_flow(client, user):
    r = client.post(reverse("accounts:password-reset"), {"email": user.email})
    assert r.status_code == 302
    assert len(mail.outbox) == 1
    link = re.search(r"http://\S+/password-reset/\S+/\S+/", mail.outbox[0].body).group(0)
    path = link.split("testserver")[1]
    r = client.get(path, follow=True)
    form_url = r.redirect_chain[-1][0]
    r = client.post(form_url, {"new_password1": "An0ther-str0ng-one", "new_password2": "An0ther-str0ng-one"})
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.check_password("An0ther-str0ng-one")


def test_password_reset_unknown_email_does_not_leak(client, db):
    r = client.post(reverse("accounts:password-reset"), {"email": "nobody@example.com"})
    assert r.status_code == 302
    assert len(mail.outbox) == 0

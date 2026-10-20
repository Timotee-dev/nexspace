"""Verifying email with a 6-digit code (works even when a mail provider rewrites links)."""
import re

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


def _code_from_outbox():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].subject).group(1)


def test_email_contains_code_and_plain_link(make_user, settings):
    settings.SITE_URL = "https://nexspace.example"
    user = make_user(email="new@example.com", verified=False)
    services.send_verification_email(user)
    email = mail.outbox[-1]
    code = _code_from_outbox()
    html = email.alternatives[0][0]
    assert code in email.body and code in html
    link = re.search(r"https://nexspace\.example/verify-email/[^\s<]+", email.body).group(0)
    assert link in html and f'href="{link}"' not in html  # plain text, so link trackers can't rewrite it


def test_correct_code_verifies(client, make_user):
    user = make_user(email="new@example.com", verified=False)
    services.send_verification_email(user)
    client.force_login(user)
    r = client.post(reverse("accounts:verify-code"), {"code": _code_from_outbox()})
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.email_verified


def test_code_accepts_spaces_and_works_once(make_user):
    user = make_user(email="new@example.com", verified=False)
    code = services.issue_code(user, "verify")
    assert services.check_code(user, "verify", f"{code[:3]} {code[3:]}")
    assert not services.check_code(user, "verify", code)  # used up


def test_wrong_codes_lock_after_five(client, make_user):
    user = make_user(email="new@example.com", verified=False)
    code = services.issue_code(user, "verify")
    wrong = "000000" if code != "000000" else "111111"
    client.force_login(user)
    for _ in range(5):
        assert "or it has expired" in client.post(reverse("accounts:verify-code"), {"code": wrong}).content.decode()
    assert not services.check_code(user, "verify", code)  # locked: even the right code needs a resend now
    user.refresh_from_db()
    assert not user.email_verified


def test_new_email_replaces_old_code_and_codes_expire(make_user):
    from apps.accounts.models import EmailCode

    user = make_user(email="new@example.com", verified=False)
    old = services.issue_code(user, "verify")
    new = services.issue_code(user, "verify")
    if old != new:
        assert not services.check_code(user, "verify", old)
    EmailCode.objects.filter(user=user).update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
    assert not services.check_code(user, "verify", new)


def test_codes_are_per_user_and_purpose(make_user):
    a = make_user(email="a@example.com", verified=False)
    b = make_user(email="b@example.com", verified=False)
    code = services.issue_code(a, "verify")
    services.issue_code(b, "verify")
    assert not services.check_code(a, "reset", code)
    if code != services.issue_code(b, "verify"):
        assert not services.check_code(b, "verify", code)
    assert services.check_code(a, "verify", code)


def test_banner_links_to_code_page(client, make_user):
    user = make_user(email="new@example.com", verified=False)
    client.force_login(user)
    assert reverse("accounts:verify-code") in client.get(reverse("core:home")).content.decode()


def test_signup_sends_code(client, department):
    client.post(reverse("accounts:signup"), {
        "full_name": "New Person", "email": "fresh@example.com", "department": department.pk, "level": 100,
        "password": PASSWORD, "confirm_password": PASSWORD,
    })
    assert mail.outbox and re.search(r"\d{6} is your NexSpace verification code", mail.outbox[-1].subject)


def test_password_reset_email_has_plain_link(client, make_user):
    make_user(email="reset@example.com")
    client.post(reverse("accounts:password-reset"), {"email": "reset@example.com"})
    html = mail.outbox[-1].alternatives[0][0]
    assert "copy this address into your browser" in html

import pytest
from django.urls import reverse

from apps.topics.models import Topic

pytestmark = pytest.mark.django_db


def test_unfinished_onboarding_redirects(client, make_user):
    user = make_user(onboarded=False)
    client.force_login(user)
    r = client.get(reverse("core:home"))
    assert r.status_code == 302 and r.url == reverse("accounts:onboarding")
    # API and logout remain reachable
    assert client.get(reverse("api-me")).status_code == 200


def test_onboarding_flow_sets_interests_and_level(client, make_user):
    user = make_user(onboarded=False)
    client.force_login(user)
    topics = list(Topic.objects.all()[:2])
    assert topics, "default topics migration should seed topics"
    r = client.post(reverse("accounts:onboarding"), {"interests": [t.pk for t in topics]})
    assert r.status_code == 302 and "step=level" in r.url
    r = client.post(reverse("accounts:onboarding") + "?step=level", {"level": 400})
    assert r.status_code == 302 and "step=spaces" in r.url
    r = client.post(reverse("accounts:onboarding") + "?step=spaces", {})
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.onboarding_completed and user.level == 400
    assert set(user.profile.interests.all()) == set(topics)
    assert client.get(reverse("core:home")).status_code == 200


def test_profile_page_and_privacy(client, make_user, other_department):
    owner = make_user(email="owner@example.com", full_name="Owner Person", matric_number="CSC/1")
    classmate = make_user(email="mate@example.com", full_name="Class Mate")
    outsider = make_user(email="out@example.com", full_name="Out Sider", department=other_department)
    url = reverse("accounts:profile", args=[owner.username])

    client.force_login(classmate)
    body = client.get(url).content.decode()
    assert "Owner Person" in body and "CSC/1" not in body  # matric hidden by default

    owner.profile.show_matric_number = True
    owner.profile.visibility = "department"
    owner.profile.save()
    assert "CSC/1" in client.get(url).content.decode()

    client.force_login(outsider)
    assert client.get(url).status_code == 404  # department-only profile


def test_profile_requires_login(client, user):
    r = client.get(reverse("accounts:profile", args=[user.username]))
    assert r.status_code == 302


def test_edit_profile(client, user):
    client.force_login(user)
    r = client.post(reverse("accounts:settings-profile"), {
        "full_name": "New Name", "username": "new_name", "level": 400, "matric_number": "",
        "bio": "Hello", "skills": "Python, python, Django", "github_url": "https://github.com/x",
        "linkedin_url": "", "portfolio_url": "",
    })
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.username == "new_name" and user.full_name == "New Name"
    assert user.profile.skills == ["Python", "Django"]


def test_edit_profile_rejects_reserved_and_taken_username(client, make_user):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    client.force_login(a)
    base = {"full_name": "A", "level": 300, "matric_number": "", "bio": "", "skills": "",
            "github_url": "", "linkedin_url": "", "portfolio_url": ""}
    assert client.post(reverse("accounts:settings-profile"), {**base, "username": "admin"}).status_code == 200
    assert client.post(reverse("accounts:settings-profile"), {**base, "username": b.username}).status_code == 200


def test_avatar_upload_rejects_non_image(client, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(user)
    fake = SimpleUploadedFile("evil.png", b"<?php echo 'hi'; ?>", content_type="image/png")
    r = client.post(reverse("accounts:settings-profile"), {
        "full_name": "A", "username": user.username, "level": 300, "matric_number": "", "bio": "",
        "skills": "", "github_url": "", "linkedin_url": "", "portfolio_url": "", "avatar": fake,
    })
    assert r.status_code == 200
    user.profile.refresh_from_db()
    assert not user.profile.avatar


def test_privacy_and_theme_settings(client, user):
    client.force_login(user)
    client.post(reverse("accounts:settings-privacy"), {"visibility": "department", "show_social_links": "on"})
    user.profile.refresh_from_db()
    assert user.profile.visibility == "department" and not user.profile.show_joined_spaces
    client.post(reverse("accounts:settings-account"), {"appearance-theme": "light", "save_appearance": "1"})
    user.profile.refresh_from_db()
    assert user.profile.theme == "light"
    assert 'data-theme-pref="light"' in client.get(reverse("core:home")).content.decode()

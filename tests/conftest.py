import pytest
from django.core.cache import cache

from apps.academics.models import Department, Faculty, Level, University
from apps.accounts.models import User


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def university(db):
    return University.objects.create(name="Test University", short_name="TU", slug="tu")


@pytest.fixture
def department(university):
    faculty = Faculty.objects.create(university=university, name="Computing", slug="computing")
    return Department.objects.create(faculty=faculty, name="Computer Science", code="CSC", slug="csc")


@pytest.fixture
def other_department(university):
    faculty = Faculty.objects.create(university=university, name="Science", slug="science")
    return Department.objects.create(faculty=faculty, name="Physics", code="PHY", slug="phy")


PASSWORD = "Str0ng-pass-phrase!"


@pytest.fixture
def make_user(department):
    def _make(email="student@example.com", verified=True, onboarded=True, **extra):
        extra.setdefault("full_name", "Test Student")
        extra.setdefault("department", department)
        extra.setdefault("level", Level.L300)
        return User.objects.create_user(
            email=email, password=PASSWORD, email_verified=verified, onboarding_completed=onboarded, **extra
        )

    return _make


@pytest.fixture
def user(make_user):
    return make_user()

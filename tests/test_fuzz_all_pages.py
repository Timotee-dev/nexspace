"""Every page on the site, as an admin and as a student, GET and POST, with junk in every common
parameter: nothing may crash (500) and nothing may redirect off the site."""
import re
import pytest
from django.urls import get_resolver, URLPattern, URLResolver
from apps.posts import services as posts
from apps.messaging import services as dm
from apps.accounts.models import RoleAssignment
from apps.accounts.services import assign_role

pytestmark = pytest.mark.django_db
JUNK = "?page=abc&after=abc&before=x&dept=abc&level=zz&days=q&since_id=x&next=//evil.example&q=%00%27%22<x>&tab=zz&category=zz&sort=zz&type=zz&course=zz&invite=x&partial=1"

def walk(resolver, prefix=""):
    for p in resolver.url_patterns:
        if isinstance(p, URLResolver):
            yield from walk(p, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern)

def test_fuzz(client, make_user, department, settings):
    settings.DEBUG = False
    owner = make_user(email="arifalotimothy@gmail.com", full_name="Owner")
    assign_role(user=owner, role=RoleAssignment.Role.SUPER_ADMIN)
    assign_role(user=owner, role=RoleAssignment.Role.DEPARTMENT_ADMIN, department=department)
    other = make_user(email="o@example.com")
    post = posts.create_post(author=other, kind="post", body="hello")
    convo = dm.start_conversation(owner, other)
    failures = []
    for raw in walk(get_resolver()):
        path = "/" + raw.lstrip("^").rstrip("$")
        if "media/" in path or "(?P" in path:
            continue
        path = re.sub(r"<int:[^>]+>", "1", path)
        path = re.sub(r"<(str|slug):(username)>", other.username, path)
        path = re.sub(r"<[^>]+>", "x", path)
        for user in (owner, other):
            client.force_login(user)
            for method in ("get", "post"):
                try:
                    r = getattr(client, method)(path + JUNK, HTTP_ACCEPT="application/json" if "api" in path else "text/html")
                except Exception as exc:
                    failures.append((method, path, repr(exc)[:150])); continue
                if r.status_code >= 500:
                    failures.append((method, path, r.status_code))
                loc = r.headers.get("Location", "")
                from urllib.parse import urlparse
                if loc and (urlparse(loc).netloc or loc.startswith(("//", "/\\"))):
                    failures.append((method, path, "open redirect " + loc))
    assert not failures, "\n".join(map(str, failures))

"""Global search across people, posts, Spaces, courses, resources and opportunities.

Uses indexed icontains lookups scoped to the user's department. Every word must
match (AND), and "CSC301" also matches "CSC 301". Anonymous posts are searchable
by content, but their author is never matched or shown.
"""
import re

from django.db.models import Q

from apps.academics.models import Course
from apps.accounts.models import User
from apps.posts.models import Post
from apps.resources.models import Resource
from apps.spaces.models import Space

GROUPS = [("courses", "Courses"), ("spaces", "Spaces"), ("resources", "Resources"),
          ("posts", "Posts"), ("opportunities", "Opportunities"), ("people", "People")]
MIN_LENGTH = 2


def _words(q):
    words = []
    for word in q.split()[:6]:
        m = re.fullmatch(r"([A-Za-z]{2,4})(\d{3})", word)
        words.append((word, f"{m.group(1)} {m.group(2)}" if m else None))
    return words


def _all_words(words, *fields):
    query = Q()
    for word, spaced in words:
        match = Q()
        for field in fields:
            match |= Q(**{f"{field}__icontains": word})
            if spaced:
                match |= Q(**{f"{field}__icontains": spaced})
        query &= match
    return query


def _course_code_phrase(q):
    m = re.search(r"\b([A-Za-z]{2,4})\s?(\d{3})\b", q)
    return f"{m.group(1).upper()} {m.group(2)}" if m else None


def search(user, q, *, only=None, limit=5):
    q = " ".join((q or "").split())[:80]
    if len(q) < MIN_LENGTH:
        return {}
    words = _words(q)
    dept = user.department_id
    results = {}

    def want(name):
        return only is None or only == name

    if want("courses"):
        results["courses"] = list(Course.objects.filter(department_id=dept, is_active=True)
                                  .filter(_all_words(words, "code", "title")).select_related("space")[:limit])
    if want("spaces"):
        results["spaces"] = list(Space.objects.filter(department_id=dept)
                                 .filter(_all_words(words, "name", "description", "course__code"))
                                 .order_by("-is_official", "-member_count")[:limit])
    if want("resources"):
        results["resources"] = list(Resource.objects.for_viewer(user).select_related("course", "session", "uploaded_by__staff_profile")
                                    .filter(_all_words(words, "title", "description", "course__code", "course__title"))
                                    .order_by("-download_count")[:limit])
    base_posts = Post.objects.for_viewer(user).with_related()
    if want("posts"):
        results["posts"] = list(base_posts.exclude(kind=Post.Kind.OPPORTUNITY)
                                .filter(_all_words(words, "title", "body", "space__name", "topics__slug"))
                                .distinct().order_by("-score", "-created_at")[:limit])
    if want("opportunities"):
        results["opportunities"] = list(base_posts.filter(kind=Post.Kind.OPPORTUNITY)
                                        .filter(_all_words(words, "title", "body", "opportunity__organization"))
                                        .distinct().order_by("-created_at")[:limit])
    if want("people"):
        people = User.objects.filter(department_id=dept, is_active=True).select_related("profile")
        code = _course_code_phrase(q)
        match = _all_words(words, "full_name", "username", "profile__skills")
        if code:  # "People associated with CSC 301": its members
            match |= Q(space_memberships__space__course__code=code)
        results["people"] = list(people.filter(match).distinct().order_by("-profile__nexscore")[:limit])
    return {k: v for k, v in results.items() if v}

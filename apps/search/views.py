from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.feed import annotate_for_user

from . import services


def _annotate_posts(results, user):
    for key in ("posts", "opportunities"):
        if key in results:
            ids = [p.pk for p in results[key]]
            from apps.posts.models import Post

            by_id = {p.pk: p for p in annotate_for_user(Post.objects.with_related().filter(pk__in=ids), user)}
            results[key] = [by_id[i] for i in ids if i in by_id]
    return results


@login_required
def search_view(request):
    q = request.GET.get("q", "").strip()
    only = request.GET.get("type") if request.GET.get("type") in dict(services.GROUPS) else None
    results = _annotate_posts(services.search(request.user, q, only=only, limit=30 if only else 5), request.user)
    context = {"q": q, "only": only, "results": results, "groups": services.GROUPS,
               "too_short": 0 < len(q) < services.MIN_LENGTH}
    if request.GET.get("partial"):
        return render(request, "search/_results.html", context)
    return render(request, "search/search.html", context)


class SearchAPIView(APIView):
    """Grouped global search. Returns lightweight results for type-ahead."""

    @extend_schema(parameters=[OpenApiParameter("q", str), OpenApiParameter("type", str)], responses={200: dict})
    def get(self, request):
        only = request.query_params.get("type") if request.query_params.get("type") in dict(services.GROUPS) else None
        results = services.search(request.user, request.query_params.get("q", ""), only=only, limit=8 if only else 5)
        out = {}
        for key, items in results.items():
            if key == "courses":
                out[key] = [{"label": f"{c.code} — {c.title}", "url": c.get_absolute_url()} for c in items]
            elif key == "spaces":
                out[key] = [{"label": s.name, "url": s.get_absolute_url()} for s in items]
            elif key == "resources":
                out[key] = [{"label": r.title, "detail": r.course.code, "url": r.get_absolute_url()} for r in items]
            elif key in ("posts", "opportunities"):
                out[key] = [{"label": (p.title or p.body)[:90], "url": p.get_absolute_url(),
                             "author": p.display_name} for p in items]
            elif key == "people":
                out[key] = [{"label": u.full_name, "detail": f"@{u.username}", "url": f"/u/{u.username}/"} for u in items]
        return Response(out)

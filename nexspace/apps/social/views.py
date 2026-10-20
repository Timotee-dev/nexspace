"""No-JavaScript fallbacks for follow buttons (JS calls the API instead)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.topics.models import Topic

from . import services


def _back(request, fallback):
    target = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return HttpResponseRedirect(target)
    return redirect(fallback)


@login_required
@require_POST
def follow_user_view(request, username):
    target = get_object_or_404(User, username=username.lower(), is_active=True)
    if not target.profile.can_be_viewed_by(request.user):
        raise Http404
    try:
        following = services.toggle_user_follow(request.user, target)
        messages.success(request, f"Following {target.full_name}." if following else f"Unfollowed {target.full_name}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back(request, f"/u/{target.username}/")


@login_required
@require_POST
def follow_topic_view(request, slug):
    topic = get_object_or_404(Topic, slug=slug, is_active=True)
    following = services.toggle_topic_follow(request.user, topic)
    messages.success(request, f"Following #{topic.slug}." if following else f"Unfollowed #{topic.slug}.")
    return _back(request, f"/t/{topic.slug}/")

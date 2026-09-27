from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import RoleAssignment, User
from apps.posts.models import Post

from . import services
from .models import ModerationAction, Report, TargetType


def _require_mod(user):
    if not user.department_id or not user.can_moderate(user.department):
        raise PermissionDenied


def _err(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


@login_required
def report_view(request, target_type, target_id):
    if target_type not in TargetType.values:
        raise Http404
    obj = services.load_target(target_type, target_id)
    if obj is None or services.target_department_id(target_type, obj) != request.user.department_id:
        raise Http404
    if request.method == "POST":
        try:
            services.submit_report(reporter=request.user, target_type=target_type, target_id=obj.pk,
                                   reason=request.POST.get("reason", ""), details=request.POST.get("details", ""))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _err(exc))
        else:
            messages.success(request, "Thanks. Moderators will review your report. The person you reported won't see who reported them.")
            return redirect(request.POST.get("next") or "/")
    return render(request, "moderation/report.html", {
        "target_type": target_type, "label": services.label(target_type, obj),
        "reasons": Report.Reason.choices, "next": request.GET.get("next", "/"),
    })


@login_required
def queue_view(request):
    _require_mod(request.user)
    return render(request, "moderation/queue.html", {
        "items": services.queue_for(request.user),
        "is_admin": request.user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=request.user.department),
        "suspended": User.objects.filter(department_id=request.user.department_id,
                                         suspended_until__isnull=False).order_by("-suspended_until")[:20],
    })


@login_required
@require_POST
def action_view(request):
    _require_mod(request.user)
    action = request.POST.get("action")
    target_type = request.POST.get("target_type")
    target_id = request.POST.get("target_id")
    note = request.POST.get("note", "")
    try:
        if action == "dismiss":
            services.dismiss(moderator=request.user, target_type=target_type, target_id=target_id, note=note)
            messages.success(request, "Reports dismissed.")
        elif action == "remove":
            services.remove_content(moderator=request.user, target_type=target_type, target_id=target_id, note=note)
            messages.success(request, "Content removed.")
        elif action in ("suspend", "ban", "unsuspend"):
            user = get_object_or_404(User, pk=request.POST.get("user_id"), department_id=request.user.department_id)
            if action == "suspend":
                services.suspend_user(moderator=request.user, user=user, days=int(request.POST.get("days", 7)), note=note)
                messages.success(request, f"{user.full_name} is suspended.")
            elif action == "unsuspend":
                services.unsuspend_user(moderator=request.user, user=user)
                messages.success(request, f"Suspension lifted for {user.full_name}.")
            else:
                services.ban_user(moderator=request.user, user=user, note=note)
                messages.success(request, f"{user.full_name} is banned.")
        else:
            messages.error(request, "Unknown action.")
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, _err(exc))
    return redirect("moderation:queue")


@login_required
@require_POST
def reveal_view(request, pk):
    _require_mod(request.user)
    post = get_object_or_404(Post, pk=pk, department_id=request.user.department_id)
    author = services.reveal_anonymous_author(moderator=request.user, post=post)
    messages.info(request, f"Anonymous post #{post.pk} was written by {author.full_name} (@{author.username}). This lookup has been logged.")
    return redirect("moderation:queue")


@login_required
def log_view(request):
    _require_mod(request.user)
    actions = ModerationAction.objects.filter(department_id=request.user.department_id).select_related("moderator")[:200]
    return render(request, "moderation/log.html", {"actions": actions})

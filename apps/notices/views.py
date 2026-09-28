from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment

from . import services
from .forms import AnnouncementForm, EventForm
from .models import Announcement


def _scope_or_403(user):
    scope = services.publish_scope(user)
    if not services.is_publisher(user):
        raise PermissionDenied
    return scope


def _target_kwargs(d):
    return {k: d.get(k) for k in ("audience", "target_level", "target_course", "target_space")}


def _initial_from_query(request):
    initial = {k: request.GET[k] for k in ("audience",) if request.GET.get(k)}
    if request.GET.get("course"):
        initial["target_course"] = Course.objects.filter(slug=request.GET["course"],
                                                         department_id=request.user.department_id).first()
    if request.GET.get("level", "").isdigit():
        initial["target_level"] = int(request.GET["level"])
    return initial


@login_required
def announcements_view(request):
    archive = request.GET.get("view") == "archive"
    if archive:
        items = list(Announcement.objects.archived().relevant_to(request.user)
                     .select_related("target_course", "target_space", "created_by")[:100])
    else:
        items = services.active_announcements_for(request.user)
    return render(request, "notices/announcements.html", {
        "items": items, "archive": archive, "can_publish": services.is_publisher(request.user),
    })


def _visible_announcement(user, pk):
    """Its audience can read it; so can department admins and moderators (they manage all of them)."""
    item = get_object_or_404(Announcement.objects.select_related("target_course", "target_space", "created_by"),
                             pk=pk, is_removed=False, department_id=user.department_id)
    if user.can_moderate(user.department) or Announcement.objects.relevant_to(user).filter(pk=pk).exists():
        return item
    raise Http404


@login_required
def announcement_detail_view(request, pk):
    item = _visible_announcement(request.user, pk)
    return render(request, "notices/announcement_detail.html", {"item": item})


@login_required
def announcement_attachment_view(request, pk):
    item = _visible_announcement(request.user, pk)
    if not item.attachment:
        raise Http404
    if hasattr(item.attachment.storage, "path"):
        try:
            return FileResponse(item.attachment.open("rb"), as_attachment=True, filename=item.attachment_name)
        except NotImplementedError:
            pass
    return HttpResponseRedirect(item.attachment.url)


@login_required
def announcement_create_view(request):
    scope = _scope_or_403(request.user)
    form = AnnouncementForm(request.POST or None, request.FILES or None, initial=_initial_from_query(request))
    form.setup_audience(request.user, scope)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            item = services.publish_announcement(
                user=request.user, title=d["title"], body=d["body"], priority=d["priority"],
                expires_at=d.get("expires_at"), attachment=d.get("attachment"), **_target_kwargs(d),
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, f"Announcement published to {item.audience_label}.")
            return redirect("notices:announcements")
    return render(request, "notices/form.html", {"form": form, "heading": "New announcement",
                                                 "submit": "Publish announcement"})


@login_required
def calendar_view(request):
    events = services.upcoming_for(request.user)
    grouped = [(month, list(items)) for month, items in
               groupby(events, key=lambda e: timezone.localtime(e.starts_at).strftime("%B %Y"))]
    return render(request, "notices/calendar.html", {
        "grouped": grouped, "countdowns": services.countdowns_for(request.user, limit=6),
        "can_publish": services.is_publisher(request.user),
    })


@login_required
def event_create_view(request):
    scope = _scope_or_403(request.user)
    initial = _initial_from_query(request)
    if request.GET.get("kind"):
        initial["kind"] = request.GET["kind"]
    form = EventForm(request.POST or None, initial=initial)
    form.setup_audience(request.user, scope)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.create_event(
                user=request.user, title=d["title"], kind=d["kind"], starts_at=d["starts_at"],
                ends_at=d.get("ends_at"), location=d.get("location", ""), description=d.get("description", ""),
                **_target_kwargs(d),
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Added to the calendar.")
            return redirect("notices:calendar")
    return render(request, "notices/form.html", {"form": form, "heading": "Add to calendar",
                                                 "submit": "Add date"})



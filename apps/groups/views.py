from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import Course
from apps.posts.services import RateLimited
from apps.spaces.services import joined_course_ids

from . import services
from .models import StudyGroup, StudyGroupMembership

DT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class GroupForm(forms.Form):
    name = forms.CharField(max_length=80)
    description = forms.CharField(max_length=300, required=False, label="What's it for? (optional)")
    course = forms.ModelChoiceField(queryset=Course.objects.none(), required=False, empty_label="No specific course")
    is_open = forms.TypedChoiceField(choices=[("1", "Open — anyone in the department can join"),
                                              ("0", "Invite-only — people join with your invite link")],
                                     coerce=lambda v: v == "1", initial="1", widget=forms.RadioSelect, label="Who can join")
    next_meeting_at = forms.DateTimeField(required=False, widget=DT, label="Next meeting (optional)")
    meeting_location = forms.CharField(max_length=150, required=False, label="Where (optional)")
    meeting_link = forms.URLField(required=False, label="Meeting link (optional)")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(department_id=user.department_id, is_active=True)


class MeetingForm(forms.Form):
    next_meeting_at = forms.DateTimeField(required=False, widget=DT, label="Next meeting")
    meeting_location = forms.CharField(max_length=150, required=False, label="Where")
    meeting_link = forms.URLField(required=False, label="Meeting link")


def _err(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


@login_required
def list_view(request):
    user = request.user
    mine_ids = set(StudyGroupMembership.objects.filter(user=user).values_list("group_id", flat=True))
    groups = StudyGroup.objects.filter(department_id=user.department_id).select_related("course")
    courses = joined_course_ids(user)
    discover = list(groups.filter(is_open=True).exclude(pk__in=mine_ids).order_by("-member_count")[:40])
    discover.sort(key=lambda g: (g.course_id not in courses, -g.member_count))
    return render(request, "groups/list.html", {"mine": list(groups.filter(pk__in=mine_ids)), "discover": discover})


@login_required
def create_view(request):
    form = GroupForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            group = services.create_group(user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, _err(exc))
        else:
            messages.success(request, "Study group created.")
            return redirect(group.get_absolute_url())
    return render(request, "groups/create.html", {"form": form})


def _group(request, pk):
    group = get_object_or_404(StudyGroup.objects.select_related("course"), pk=pk)
    if group.department_id != request.user.department_id:
        raise Http404
    return group


@login_required
def detail_view(request, pk):
    group = _group(request, pk)
    member = services.is_member(request.user, group)
    if not group.is_open and not member and request.GET.get("invite") != group.invite_code:
        raise Http404  # invite-only groups are invisible without the link
    admin = services.is_admin(request.user, group)
    return render(request, "groups/detail.html", {
        "group": group, "is_member": member, "is_admin": admin,
        "members": list(group.memberships.select_related("user__profile").order_by("-is_admin", "joined_at")),
        "messages_list": list(group.messages.select_related("author__profile").order_by("-created_at")[:50])[::-1] if member else [],
        "meeting_form": MeetingForm(initial={"next_meeting_at": group.next_meeting_at,
                                             "meeting_location": group.meeting_location,
                                             "meeting_link": group.meeting_link}) if admin else None,
        "invite_url": request.build_absolute_uri(f"{group.get_absolute_url()}?invite={group.invite_code}") if member else "",
        "invite": request.GET.get("invite", ""),
    })


@login_required
@require_POST
def membership_view(request, pk):
    group = _group(request, pk)
    try:
        if request.POST.get("action") == "leave":
            services.leave(user=request.user, group=group)
            messages.success(request, f"You left {group.name}.")
            return redirect("groups:list")
        services.join(user=request.user, group=group, invite_code=request.POST.get("invite"))
        messages.success(request, f"You joined {group.name}.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _err(exc))
    return redirect(group.get_absolute_url())


@login_required
@require_POST
def message_view(request, pk):
    group = _group(request, pk)
    try:
        services.post_message(user=request.user, group=group, body=request.POST.get("body", ""))
    except (ValidationError, PermissionDenied, RateLimited) as exc:
        messages.error(request, _err(exc))
    return redirect(f"{group.get_absolute_url()}#discussion")


@login_required
@require_POST
def meeting_view(request, pk):
    group = _group(request, pk)
    form = MeetingForm(request.POST)
    if form.is_valid():
        try:
            services.update_meeting(user=request.user, group=group, **form.cleaned_data)
            messages.success(request, "Meeting updated. Members have been notified.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _err(exc))
    else:
        messages.error(request, "Check the meeting details.")
    return redirect(group.get_absolute_url())

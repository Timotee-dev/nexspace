from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.academics.models import Course, Level
from apps.accounts.models import RoleAssignment, User
from apps.posts.feed import annotate_for_user
from apps.posts.models import Post

from . import services
from apps.posts.services import RateLimited

from .models import Space, SpaceJoinRequest, SpaceMembership

SPACE_TABS = [("posts", "Posts"), ("questions", "Questions"), ("members", "Members"), ("about", "About")]
COURSE_TABS = [
    ("discussions", "Discussions"), ("resources", "Resources"), ("past-questions", "Past questions"),
    ("questions", "Questions"), ("people", "People"), ("upcoming", "Upcoming"),
]


def _back(request, fallback):
    target = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return HttpResponseRedirect(target)
    return redirect(fallback)


def _dept_space(request, **lookup):
    space = get_object_or_404(Space.objects.select_related("course", "department"), **lookup)
    if space.department_id != request.user.department_id:
        raise Http404
    return space


@login_required
def space_list_view(request):
    """Spaces are clubs and activities (sports, games, music, faith...). Course discussions live in Courses;
    the department and level Spaces are shown separately as "Your class"."""
    memberships = {m.space_id: m for m in SpaceMembership.objects.filter(user=request.user)}
    pending = services.pending_request_ids(request.user)
    spaces = list(Space.objects.filter(department_id=request.user.department_id).exclude(kind=Space.Kind.COURSE)
                  .order_by("-member_count", "name"))
    for s in spaces:
        s.membership = memberships.get(s.pk)
        s.pending = s.pk in pending
    category = request.GET.get("category", "")
    if category not in Space.Category.values:
        category = ""
    clubs = [s for s in spaces if s.kind == Space.Kind.COMMUNITY and (not category or s.category == category)]
    return render(request, "spaces/list.html", {
        "mine": [s for s in clubs if s.membership],
        "discover": [s for s in clubs if not s.membership],
        "official": [s for s in spaces if s.kind != Space.Kind.COMMUNITY],
        "categories": Space.Category.choices, "category": category,
        "can_create": services.can_create_space(request.user),
    })


@login_required
def course_list_view(request):
    level = request.GET.get("level")
    joined = services.joined_course_ids(request.user)
    courses = Course.objects.filter(department_id=request.user.department_id, is_active=True).select_related("space")
    if level and level.isdigit():
        courses = courses.filter(level=int(level))
    grouped = {}
    for course in courses:
        course.joined = course.pk in joined
        grouped.setdefault(course.get_level_display(), []).append(course)
    return render(request, "spaces/courses.html", {
        "grouped": grouped, "levels": Level.choices, "level": level,
    })


def _space_page(request, space, tab):
    user = request.user
    membership = SpaceMembership.objects.filter(space=space, user=user).first()
    course = space.course
    tabs = COURSE_TABS if course else SPACE_TABS
    tab_keys = [k for k, _ in tabs]
    if tab not in tab_keys:
        tab = tab_keys[0]
    can_manage = services.can_manage(user, space)
    can_see = services.can_see_inside(user, space)
    if not can_see and course:  # course not joined: materials are for everyone, discussions for members
        tabs = [(k, label) for k, label in tabs if k in ("resources", "past-questions", "upcoming")]
        if tab not in [k for k, _ in tabs]:
            tab = tabs[0][0]
    elif not can_see:  # private Space: outsiders only see About and the request button
        tabs = [("about", "About")]
        tab = "about"
    elif can_manage and space.requires_approval:
        tabs = list(tabs) + [("requests", "Requests")]
        if request.GET.get("tab") == "requests":
            tab = "requests"
    q = request.GET.get("q", "").strip()[:80]
    context = {
        "space": space, "course": course, "membership": membership, "tabs": tabs, "tab": tab, "q": q,
        "can_manage": can_manage, "can_see": can_see,
        "pending_request": SpaceJoinRequest.objects.filter(space=space, user=user, status="pending").exists(),
        "request_count": space.join_requests.filter(status="pending").count() if can_manage else 0,
    }
    if course:
        from apps.accounts.services import level_reps

        context["reps"] = list(level_reps(course.department, course.level).select_related("profile"))
        context["is_rep"] = user.has_role(RoleAssignment.Role.COURSE_REP, course=course)

    if tab in ("posts", "discussions", "questions"):
        posts = Post.objects.for_viewer(user).with_related().filter(space=space)
        if tab == "questions":
            posts = posts.filter(kind=Post.Kind.QUESTION)
        if q:
            posts = posts.filter(Q(body__icontains=q) | Q(title__icontains=q))
        context["posts"] = list(annotate_for_user(posts, user)[:40])
    elif tab == "requests":
        context["requests"] = list(space.join_requests.filter(status="pending").select_related("user__profile"))
    elif tab in ("members", "people"):
        context["members"] = list(
            SpaceMembership.objects.filter(space=space).select_related("user__profile")
            .order_by("-role", "user__full_name")[:200]
        )
    elif tab in ("resources", "past-questions"):
        from apps.resources.models import Resource
        from apps.resources.services import search

        qs = Resource.objects.for_viewer(user).filter(course=course).select_related("session", "uploaded_by__staff_profile")
        if tab == "past-questions":
            qs = qs.filter(resource_type=Resource.Type.PAST_QUESTION)
        context["resources"] = list(search(qs, q=q, sort=request.GET.get("sort", "useful"))[:60])
        context["sort"] = request.GET.get("sort", "useful")
    elif tab == "upcoming":
        from apps.notices.models import AcademicEvent, Audience
        from django.utils import timezone

        context["events"] = list(
            AcademicEvent.objects.filter(audience=Audience.COURSE, target_course=course,
                                         starts_at__gte=timezone.now()).order_by("starts_at")[:30]
        )
    return render(request, "spaces/detail.html", context)


@login_required
def space_detail_view(request, slug):
    space = _dept_space(request, slug=slug, department_id=request.user.department_id)
    if space.course_id:
        return redirect(space.course.get_absolute_url())
    return _space_page(request, space, request.GET.get("tab", "posts"))


@login_required
def course_detail_view(request, slug):
    course = get_object_or_404(Course, slug=slug, department_id=request.user.department_id)
    space = services.ensure_course_space(course)
    return _space_page(request, space, request.GET.get("tab", "discussions"))


@login_required
@require_POST
def membership_view(request, pk):
    space = _dept_space(request, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "join":
            result = services.request_to_join(request.user, space, request.POST.get("message", ""))
            messages.success(request, {
                "joined": f"Joined {space.name}.", "member": f"You're already in {space.name}.",
                "requested": f"Request sent. {space.name}'s managers will review it.",
                "pending": "Your request is still waiting for approval.",
            }[result])
        elif action == "cancel":
            services.cancel_request(request.user, space)
            messages.success(request, "Request cancelled.")
        elif action == "leave":
            services.leave(request.user, space)
            messages.success(request, f"Left {space.name}.")
        elif action in ("mute", "unmute"):
            services.set_muted(request.user, space, action == "mute")
            messages.success(request, f"{'Muted' if action == 'mute' else 'Unmuted'} {space.name}.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    except Exception as exc:  # rate limit
        from apps.posts.services import RateLimited

        if not isinstance(exc, RateLimited):
            raise
        messages.error(request, str(exc))
    return _back(request, space.get_absolute_url())


@login_required
@require_POST
def request_decision_view(request, pk, request_id):
    space = _dept_space(request, pk=pk)
    join_request = get_object_or_404(SpaceJoinRequest.objects.select_related("user", "space"), pk=request_id, space=space)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide_request(manager=request.user, join_request=join_request, approve=approve)
        messages.success(request, f"{join_request.user.full_name} {'added' if approve else 'declined'}.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect(f"{space.get_absolute_url()}?tab=requests")


@login_required
@require_POST
def remove_member_view(request, pk, user_id):
    space = _dept_space(request, pk=pk)
    member = get_object_or_404(User, pk=user_id)
    try:
        services.remove_member(manager=request.user, space=space, user=member)
        messages.success(request, f"{member.full_name} was removed from {space.name}.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect(f"{space.get_absolute_url()}?tab={'people' if space.course_id else 'members'}")


@login_required
def space_create_view(request):
    if not services.can_create_space(request.user):
        raise PermissionDenied
    errors = []
    data = request.POST if request.method == "POST" else {}
    if request.method == "POST":
        try:
            space = services.create_space(
                user=request.user, name=data.get("name", ""), description=data.get("description", ""),
                rules=data.get("rules", ""), icon=data.get("icon", ""), category=data.get("category", ""),
                requires_approval=data.get("private") == "on",
            )
        except (ValidationError, PermissionDenied) as exc:
            errors = getattr(exc, "messages", [str(exc)])
        except RateLimited as exc:
            errors = [str(exc)]
        else:
            messages.success(request, f"{space.name} is ready. Invite people to join.")
            return redirect(space.get_absolute_url())
    return render(request, "spaces/create.html", {"errors": errors, "data": data, "categories": Space.Category.choices})

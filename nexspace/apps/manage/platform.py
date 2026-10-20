"""Platform dashboard (/platform/) — the whole of NexSpace, every department, for platform admins only.

Overview   system health, global numbers, every department, staff to verify, latest activity
Activity   one timeline of sign-ups, posts, comments, uploads, reports, moderation and admin actions
People     search every account in every department and jump to managing it
Staff      verify staff sign-ups from every department
Audit      every sensitive action (role changes, bans, identity reveals, deletions…)
Database   row counts for every table, each linking to the Django admin where it can be edited
"""
import time
from datetime import timedelta
from functools import wraps

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.academics.models import Department
from apps.accounts.models import StaffProfile, User
from apps.accounts.services import verify_staff
from apps.core.models import AuditLog
from apps.moderation.models import ModerationAction, Report
from apps.posts.models import Comment, Post
from apps.resources.models import Resource

AUDIT_PHRASES = {
    "account.deleted": "deleted their account", "announcement.removed": "removed an announcement",
    "anonymous.revealed": "revealed who wrote an anonymous post", "course.saved": "saved a course",
    "department.created": "created a department", "role.assigned": "gave a role to", "role.revoked": "removed a role from",
    "space.saved": "saved a Space", "staff.requested": "signed up as staff", "staff.verified": "verified staff account",
    "staff.rejected": "rejected staff account", "user.banned": "banned", "user.unbanned": "unbanned",
    "user.suspended": "suspended", "user.unsuspended": "lifted the suspension of",
    "user.email_verified_manually": "manually verified the email of",
}


def describe(log, users=None):
    """Plain-English line for an audit log entry, e.g. "Ada Admin gave a role to Funmi Adebayo (lecturer)"."""
    actor = log.actor.full_name if log.actor else "NexSpace"
    phrase = AUDIT_PHRASES.get(log.action, log.action)
    target = ""
    if log.target_type == "accounts.user" and users is not None:
        person = users.get(int(log.target_id)) if str(log.target_id).isdigit() else None
        if person and (not log.actor or person.pk != log.actor_id):
            target = f" {person.full_name}"
    detail = log.metadata.get("role") or log.metadata.get("position") or ""
    return f"{actor} {phrase}{target}" + (f" ({detail.replace('_', ' ')})" if detail else "")


def _users_for(logs):
    ids = {int(l.target_id) for l in logs if l.target_type == "accounts.user" and str(l.target_id).isdigit()}
    return User.objects.in_bulk(ids)


ACTIVITY_TYPES = [("signup", "Sign-ups"), ("post", "Posts"), ("comment", "Comments"), ("upload", "Uploads"),
                  ("report", "Reports"), ("moderation", "Moderation"), ("admin", "Admin actions")]


def platform_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_platform_admin:
            from .views import _admin_sign_in

            return _admin_sign_in(request)
        return view(request, *args, **kwargs)
    return wrapper


def _render(request, template, context, section):
    context.update({"section": section, "pending_staff_count":
                    StaffProfile.objects.filter(status=StaffProfile.Status.PENDING).count()})
    return render(request, template, context)


# --- Health ------------------------------------------------------------------------
def system_health():
    checks = []
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks.append(("Database", True, f"{(time.perf_counter() - started) * 1000:.0f} ms · {connection.vendor}"))
    except Exception as exc:  # pragma: no cover - shown on the page
        checks.append(("Database", False, str(exc)[:120]))
    try:
        cache.set("health-check", 1, 10)
        checks.append(("Cache", cache.get("health-check") == 1, settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]))
    except Exception as exc:  # pragma: no cover
        checks.append(("Cache", False, str(exc)[:120]))
    checks += [
        ("Email", bool(settings.BREVO_API_KEY), "Brevo" if settings.BREVO_API_KEY else "Not set: emails only print to the server log"),
        ("File storage", bool(settings.FILE_STORAGE_NAME),
         f"{settings.FILE_STORAGE_NAME} · up to {settings.MAX_DOCUMENT_MB} MB per file" if settings.FILE_STORAGE_NAME
         else "Local disk: uploads are lost on each Render deploy"),
        ("Push notifications", settings.PUSH_ENABLED, "On" if settings.PUSH_ENABLED else "Off (no VAPID keys)"),
        ("NexAI answers", settings.NEXAI_ENABLED, settings.NEXAI_MODEL if settings.NEXAI_ENABLED else "Search-only (no API key)"),
        ("Production mode", not settings.DEBUG, "DEBUG is off" if not settings.DEBUG else "DEBUG is ON — never on the live site"),
    ]
    last = cache.get("last-scheduled-run")
    if last:
        at = timezone.datetime.fromisoformat(last["at"])
        fresh = timezone.now() - at < timedelta(minutes=45)
        checks.append(("Scheduled jobs", fresh, f"Last ran {timezone.localtime(at):%d %b, %H:%M}"))
    else:
        checks.append(("Scheduled jobs", False, "Never ran: set up cron-job.org (DEPLOY.md 5b)"))
    return checks


# --- Activity feed ---------------------------------------------------------------------
def activity(*, department=None, kind=None, limit=60):
    items = []

    def want(k):
        return kind in (None, "", k)

    dept_q = (lambda field: Q(**{field: department})) if department else (lambda field: Q())
    if want("signup"):
        for u in User.objects.filter(dept_q("department")).select_related("department", "staff_profile").order_by("-date_joined")[:limit]:
            label = f"{u.full_name} joined" + (f" as {u.staff_profile.get_position_display()} (pending)"
                                              if hasattr(u, "staff_profile") and u.staff_profile.status == "pending" else "")
            items.append({"at": u.date_joined, "kind": "signup", "text": label, "dept": u.department,
                          "url": f"/platform/people/?q={u.email}"})
    if want("post"):
        for p in Post.objects.filter(dept_q("department")).select_related("author", "department").order_by("-created_at")[:limit]:
            who = "Anonymous Student" if p.is_anonymous else p.author.full_name
            items.append({"at": p.created_at, "kind": "post", "dept": p.department, "url": p.get_absolute_url(),
                          "text": f"{who} posted a {p.get_kind_display().lower()}: {(p.title or p.body)[:80]}"
                                  + (" (deleted)" if p.is_deleted else "")})
    if want("comment"):
        for c in Comment.objects.filter(dept_q("post__department")).select_related("author", "post__department").order_by("-created_at")[:limit]:
            items.append({"at": c.created_at, "kind": "comment", "dept": c.post.department,
                          "url": f"{c.post.get_absolute_url()}#c-{c.pk}",
                          "text": f"{c.author.full_name} commented: {c.body[:80] or '[deleted]'}"})
    if want("upload"):
        for r in Resource.objects.filter(dept_q("course__department")).select_related("uploaded_by", "course__department").order_by("-created_at")[:limit]:
            items.append({"at": r.created_at, "kind": "upload", "dept": r.course.department, "url": r.get_absolute_url(),
                          "text": f"{r.uploaded_by.full_name if r.uploaded_by else 'Someone'} uploaded {r.title} ({r.course.code})"})
    if want("report"):
        for r in Report.objects.filter(dept_q("department")).select_related("reporter", "department").order_by("-created_at")[:limit]:
            items.append({"at": r.created_at, "kind": "report", "dept": r.department, "url": "/moderation/",
                          "text": f"{r.get_target_type_display()} reported for {r.get_reason_display().lower()} ({r.get_status_display().lower()})"})
    if want("moderation"):
        for a in ModerationAction.objects.filter(dept_q("department")).select_related("moderator", "department").order_by("-created_at")[:limit]:
            items.append({"at": a.created_at, "kind": "moderation", "dept": a.department, "url": "/moderation/log/",
                          "text": f"{a.moderator.full_name if a.moderator else 'NexSpace'}: {a.get_action_display().lower()} — {a.target_label}"})
    if want("admin"):
        logs = AuditLog.objects.select_related("actor").order_by("-created_at")
        if department:
            logs = logs.filter(actor__department=department)
        logs = list(logs[:limit])
        users = _users_for(logs)
        for a in logs:
            items.append({"at": a.created_at, "kind": "admin", "dept": getattr(a.actor, "department", None),
                          "url": "/platform/audit/", "text": describe(a, users)})
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


# --- Views ------------------------------------------------------------------------------
@platform_required
def overview_view(request):
    now = timezone.now()
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    from apps.nexai.models import Usage
    from apps.notifications.models import Notification

    totals = {
        "users": User.objects.filter(is_active=True).count(),
        "new_7": User.objects.filter(date_joined__gte=week).count(),
        "active_7": User.objects.filter(last_seen_at__gte=week).count(),
        "posts_7": Post.objects.filter(created_at__gte=week).count(),
        "comments_7": Comment.objects.filter(created_at__gte=week).count(),
        "resources": Resource.objects.filter(is_removed=False).count(),
        "open_reports": Report.objects.filter(status=Report.Status.OPEN).count(),
        "nexai_7": Usage.objects.filter(created_at__gte=week).count(),
        "banned": User.objects.filter(is_active=False).exclude(email__endswith="@deleted.invalid").count(),
        "suspended": User.objects.filter(suspended_until__gt=now).count(),
        "push_pending": Notification.objects.filter(push_status="pending").count(),
        "unindexed": Resource.objects.filter(indexed_at__isnull=True, is_removed=False).count(),
    }
    departments = (Department.objects.select_related("faculty__university")
                   .annotate(member_count=Count("members", filter=Q(members__is_active=True), distinct=True),
                             active_count=Count("members", filter=Q(members__last_seen_at__gte=week), distinct=True),
                             post_count=Count("posts", filter=Q(posts__created_at__gte=month), distinct=True))
                   .order_by("-member_count"))
    open_reports = dict(Report.objects.filter(status="open").values_list("department").annotate(n=Count("id")))
    pending_by_dept = dict(StaffProfile.objects.filter(status="pending").values_list("user__department").annotate(n=Count("id")))
    for d in departments:
        d.open_reports, d.pending_staff = open_reports.get(d.pk, 0), pending_by_dept.get(d.pk, 0)
    return _render(request, "platform/overview.html", {
        "health": system_health(), "totals": totals, "departments": departments,
        "pending": StaffProfile.objects.filter(status="pending").select_related("user__department")
                   .prefetch_related("requested_courses")[:8],
        "activity": activity(limit=15), "last_run": cache.get("last-scheduled-run"),
    }, "overview")


@platform_required
@require_POST
def run_jobs_view(request):
    from apps.core.scheduled import run_all

    result = run_all()
    messages.success(request, f"Scheduled jobs ran: {result}")
    return redirect("platform:overview")


@platform_required
def activity_view(request):
    dept = Department.objects.filter(pk=request.GET.get("dept")).first() if request.GET.get("dept", "").isdigit() else None
    kind = request.GET.get("kind") if request.GET.get("kind") in dict(ACTIVITY_TYPES) else None
    return _render(request, "platform/activity.html", {
        "items": activity(department=dept, kind=kind, limit=150), "departments": Department.objects.all(),
        "dept": dept, "kind": kind, "types": ACTIVITY_TYPES,
    }, "activity")


@platform_required
def people_view(request):
    qs = User.objects.select_related("department__faculty__university", "staff_profile").annotate(
        roles=Count("role_assignments")).order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(username__icontains=q)
                       | Q(matric_number__icontains=q))
    filters = {
        "staff": Q(staff_profile__isnull=False), "pending_staff": Q(staff_profile__status="pending"),
        "admins": Q(is_superuser=True) | Q(role_assignments__role__in=["super_admin", "department_admin"]),
        "suspended": Q(suspended_until__gt=timezone.now()), "banned": Q(is_active=False),
        "unverified": Q(email_verified=False), "no_department": Q(department__isnull=True),
    }
    show = request.GET.get("show", "")
    if show in filters:
        qs = qs.filter(filters[show]).distinct()
    if request.GET.get("dept", "").isdigit():
        qs = qs.filter(department_id=int(request.GET["dept"]))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return _render(request, "platform/people.html", {
        "page": page, "q": q, "show": show, "departments": Department.objects.all(),
        "dept": request.GET.get("dept", ""),
        "filters": [("staff", "Staff"), ("pending_staff", "Staff to verify"), ("admins", "Admins"),
                    ("suspended", "Suspended"), ("banned", "Banned / deleted"), ("unverified", "Email not verified"),
                    ("no_department", "No department")],
    }, "people")


@platform_required
def staff_view(request):
    staff = StaffProfile.objects.select_related("user__department", "decided_by").prefetch_related("requested_courses")
    pending = list(staff.filter(status="pending").order_by("created_at"))
    from apps.academics.models import Course

    for s in pending:
        s.course_choices = list(Course.objects.filter(department=s.user.department, is_active=True)
                                .order_by("level", "code")) if s.position == "lecturer" else []
        s.picked = {c.pk for c in s.requested_courses.all()}
    return _render(request, "platform/staff.html", {
        "pending": pending,
        "recent": staff.exclude(status="pending").order_by("-decided_at")[:30],
    }, "staff")


@platform_required
@require_POST
def staff_decision_view(request, pk):
    staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        from apps.academics.models import Course

        picked = None
        if staff.position == "lecturer" and "courses_submitted" in request.POST:
            picked = list(Course.objects.filter(pk__in=request.POST.getlist("courses"),
                                                department=staff.user.department))
        verify_staff(admin=request.user, staff=staff, approve=approve, note=request.POST.get("note", ""),
                     courses=picked)
        messages.success(request, f"{staff.user.full_name} "
                                  f"{'verified as ' + staff.get_position_display() if approve else 'not verified'}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc) or "You can't verify this account.")
    return redirect(request.POST.get("next") or "platform:staff")


@platform_required
def audit_view(request):
    logs = AuditLog.objects.select_related("actor").order_by("-created_at")
    action = request.GET.get("action", "")
    if action:
        logs = logs.filter(action__startswith=action)
    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    users = _users_for(page.object_list)
    for log in page.object_list:
        log.summary = describe(log, users)
    return _render(request, "platform/audit.html", {
        "page": page, "action": action,
        "actions": sorted(set(AuditLog.objects.values_list("action", flat=True))),
    }, "audit")


@platform_required
def database_view(request):
    tables = []
    for model in apps.get_models():
        meta = model._meta
        if meta.app_label in {"sessions", "contenttypes"}:
            continue
        try:
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
        except NoReverseMatch:
            url = None
        tables.append({"app": meta.app_config.verbose_name, "name": meta.verbose_name_plural.capitalize(),
                       "count": model.objects.count(), "url": url})
    tables.sort(key=lambda t: (t["app"], t["name"]))
    return _render(request, "platform/database.html", {"tables": tables}, "database")

"""Personal dashboard (/dashboard/): one page that assembles a section for every role a person holds.

HOD / department admin  → department at a glance, staff to verify, gaps (courses with no lecturer or rep)
Lecturer / course rep   → a card per course: members, materials, downloads, unanswered questions, next date
Level adviser           → their level: students, verification, active students, upcoming dates, notices
Exam officer            → upcoming exams and tests, and courses with no exam scheduled yet
Moderator               → open reports
Any Space manager       → people waiting to join their Spaces
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.academics.models import Course
from apps.accounts.models import RoleAssignment, StaffProfile, User
from apps.moderation.models import Report
from apps.notices.models import AcademicEvent, Announcement, Audience
from apps.posts.models import Post
from apps.resources.models import Resource, ResourceDownload
from apps.spaces.models import Space, SpaceJoinRequest, SpaceMembership

R = RoleAssignment.Role


def _course_cards(user, courses):
    now = timezone.now()
    since = now - timedelta(days=30)
    cards = []
    for course in courses:
        space = getattr(course, "space", None)
        unanswered = (Post.objects.visible().filter(space=space, kind=Post.Kind.QUESTION, comment_count=0)
                      if space else Post.objects.none())
        cards.append({
            "course": course,
            "members": space.member_count if space else 0,
            "resources": course.resources.filter(is_removed=False).count(),
            "downloads_30": ResourceDownload.objects.filter(resource__course=course, day__gte=since.date()).count(),
            "unanswered": unanswered.count(),
            "next_event": AcademicEvent.objects.filter(audience=Audience.COURSE, target_course=course,
                                                       starts_at__gte=now).order_by("starts_at").first(),
            "is_lecturer": user.role_assignments.filter(role=R.LECTURER, course=course).exists(),
        })
    return cards


@login_required
def dashboard_view(request):
    user = request.user
    if not user.has_dashboard:
        return redirect("core:home")
    dept = user.department
    now = timezone.now()
    assignments = user.role_assignments.select_related("course", "department")
    is_admin = user.has_role(R.DEPARTMENT_ADMIN, department=dept)
    ctx = {"staff": user.staff, "is_admin": is_admin, "sections": []}

    # --- HOD / department admin --------------------------------------------------
    if is_admin:
        from .analytics import overview

        a = overview(dept, 30)
        active = Course.objects.filter(department=dept, is_active=True)
        ctx["admin"] = {
            "totals": a["totals"],
            "pending_staff": list(StaffProfile.objects.filter(user__department=dept, status=StaffProfile.Status.PENDING)
                                  .select_related("user").prefetch_related("requested_courses")[:6]),
            "can_verify": user.is_platform_admin or settings.STAFF_VERIFICATION == "department",
            "no_lecturer": list(active.exclude(role_assignments__role=R.LECTURER)[:8]),
            "no_rep": list(active.exclude(role_assignments__role=R.COURSE_REP)[:8]),
        }
        ctx["sections"].append("admin")

    # --- Lecturer / course rep -------------------------------------------------------
    course_ids = assignments.filter(role__in=[R.LECTURER, R.COURSE_REP], course__isnull=False).values_list(
        "course_id", flat=True)
    my_courses = list(Course.objects.filter(pk__in=course_ids).select_related("space").order_by("level", "code"))
    if my_courses:
        ctx["course_cards"] = _course_cards(user, my_courses)
        spaces = [c.space.pk for c in my_courses if hasattr(c, "space")]
        ctx["unanswered"] = list(Post.objects.visible().filter(space_id__in=spaces, kind=Post.Kind.QUESTION,
                                                               comment_count=0)
                                 .select_related("space__course").order_by("-created_at")[:6])
        ctx["sections"].append("courses")

    # --- Level adviser -------------------------------------------------------------
    levels = sorted(set(assignments.filter(role=R.LEVEL_ADVISER).values_list("level", flat=True)))
    if levels:
        level_blocks = []
        for level in levels:
            students = User.objects.filter(department=dept, level=level, is_active=True, staff_profile__isnull=True)
            total = students.count()
            level_blocks.append({
                "level": level,
                "students": total,
                "verified": students.filter(email_verified=True).count(),
                "active_7": students.filter(last_seen_at__gte=now - timedelta(days=7)).count(),
                "unverified": list(students.filter(email_verified=False).order_by("full_name")[:8]),
                "events": list(AcademicEvent.objects.filter(department=dept).filter(
                    Q(audience=Audience.LEVEL, target_level=level) | Q(audience=Audience.DEPARTMENT),
                    starts_at__gte=now).order_by("starts_at")[:5]),
                "notices": Announcement.objects.active().filter(department=dept, audience=Audience.LEVEL,
                                                                target_level=level).count(),
                "space": Space.objects.filter(department=dept, kind=Space.Kind.LEVEL, level=level).first(),
            })
        ctx["levels"] = level_blocks
        ctx["sections"].append("levels")

    # --- Exam officer (and HOD) ----------------------------------------------------------
    if is_admin or assignments.filter(role=R.EXAM_OFFICER).exists():
        exams = (AcademicEvent.objects.filter(department=dept, kind__in=[AcademicEvent.Kind.EXAM, AcademicEvent.Kind.TEST],
                                              starts_at__gte=now, starts_at__lte=now + timedelta(days=60))
                 .select_related("target_course").order_by("starts_at"))
        scheduled = set(exams.filter(kind=AcademicEvent.Kind.EXAM).values_list("target_course_id", flat=True))
        ctx["exams"] = {
            "upcoming": list(exams[:12]),
            "unscheduled": list(Course.objects.filter(department=dept, is_active=True).exclude(pk__in=scheduled)
                                .order_by("level", "code")[:12]),
        }
        ctx["sections"].append("exams")

    # --- Moderation & Space requests ----------------------------------------------------
    if user.can_moderate(dept) and not is_admin:  # admins already see open reports in their overview
        ctx["open_reports"] = Report.objects.filter(department=dept, status=Report.Status.OPEN).count()
        ctx["sections"].append("moderation")
    managed = SpaceMembership.objects.filter(user=user, role=SpaceMembership.Role.MODERATOR).values_list("space_id", flat=True)
    requests = (SpaceJoinRequest.objects.filter(status="pending")
                .filter(Q(space_id__in=managed) | Q(space__course_id__in=course_ids)
                        | (Q(space__department=dept) if is_admin else Q(pk__in=[])))
                .values("space__name", "space__slug", "space__course__slug").annotate(n=Count("id")).order_by("-n"))
    ctx["join_requests"] = list(requests[:8])

    ctx["my_announcements"] = list(Announcement.objects.active().filter(created_by=user).order_by("-created_at")[:4])
    ctx["my_resources"] = Resource.objects.filter(uploaded_by=user, is_removed=False).count()
    return render(request, "manage/dashboard.html", ctx)

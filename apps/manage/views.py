"""Custom admin dashboard (spec Section 36) for department admins and super admins.

Every view checks permissions server-side. Department admins manage their own
department; super admins can switch department and manage institutions and topics.
"""
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.academics.models import AcademicSession, Course, Department, Faculty, Level, University
from apps.accounts.models import RoleAssignment, User
from apps.accounts.services import assign_role, revoke_role
from apps.core.models import audit
from apps.moderation import services as moderation
from apps.notices.models import Announcement
from apps.spaces.models import Space
from apps.topics.models import Topic

from . import analytics


# --- Access ----------------------------------------------------------------------
def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        department = user.department
        if user.is_platform_admin and request.GET.get("dept"):
            department = get_object_or_404(Department, pk=request.GET["dept"])
            request.session["manage_dept"] = department.pk
        elif user.is_platform_admin and request.session.get("manage_dept"):
            department = Department.objects.filter(pk=request.session["manage_dept"]).first() or department
        if department is None or not user.has_role(RoleAssignment.Role.DEPARTMENT_ADMIN, department=department):
            raise PermissionDenied
        request.manage_department = department
        return view(request, *args, **kwargs)
    return wrapper


def super_required(view):
    @wraps(view)
    @admin_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_platform_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _render(request, template, context, section):
    context.update({
        "section": section, "dept": request.manage_department, "is_super": request.user.is_platform_admin,
        "departments": Department.objects.select_related("faculty__university") if request.user.is_platform_admin else [],
    })
    return render(request, template, context)


def _err(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


# --- Overview & analytics ------------------------------------------------------------
@admin_required
def overview_view(request):
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    days = days if days in (7, 30, 90) else 30
    return _render(request, "manage/overview.html", {"a": analytics.overview(request.manage_department, days)},
                   "overview")


# --- Users -------------------------------------------------------------------------
@admin_required
def users_view(request):
    dept = request.manage_department
    qs = User.objects.filter(department=dept).select_related("profile").annotate(
        roles=Count("role_assignments")).order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(username__icontains=q)
                       | Q(matric_number__icontains=q))
    status = request.GET.get("status", "")
    if status == "unverified":
        qs = qs.filter(email_verified=False)
    elif status == "suspended":
        from django.utils import timezone

        qs = qs.filter(suspended_until__gt=timezone.now())
    elif status == "banned":
        qs = qs.filter(is_active=False)
    elif status == "staff":
        qs = qs.filter(roles__gt=0)
    if request.GET.get("level", "").isdigit():
        qs = qs.filter(level=int(request.GET["level"]))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return _render(request, "manage/users.html", {"page": page, "q": q, "status": status,
                                                  "level": request.GET.get("level", ""), "levels": Level.choices},
                   "users")


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=[(v, l) for v, l in RoleAssignment.Role.choices if v != "super_admin"])
    course = forms.ModelChoiceField(queryset=Course.objects.none(), required=False,
                                    help_text="Required for course reps", empty_label="Choose a course")

    def __init__(self, *args, department, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(department=department, is_active=True)

    def clean(self):
        data = super().clean()
        if data.get("role") == RoleAssignment.Role.COURSE_REP and not data.get("course"):
            self.add_error("course", "Choose which course they represent.")
        return data


@admin_required
def user_detail_view(request, pk):
    dept = request.manage_department
    member = get_object_or_404(User.objects.select_related("profile"), pk=pk, department=dept)
    form = RoleForm(department=dept)
    return _render(request, "manage/user_detail.html", {
        "member": member, "role_form": form,
        "roles": member.role_assignments.select_related("course", "department"),
        "stats": {
            "posts": member.posts.filter(is_deleted=False).count(),
            "comments": member.comments.filter(is_deleted=False).count(),
            "resources": member.resources.filter(is_removed=False).count(),
            "reports_against": moderation.Report.objects.filter(target_type="user", target_id=member.pk).count(),
        },
    }, "users")


@admin_required
@require_POST
def user_action_view(request, pk):
    dept = request.manage_department
    member = get_object_or_404(User, pk=pk, department=dept)
    action = request.POST.get("action")
    try:
        if action == "assign_role":
            form = RoleForm(request.POST, department=dept)
            if not form.is_valid():
                raise ValidationError(" ".join(e for errs in form.errors.values() for e in errs))
            role, course = form.cleaned_data["role"], form.cleaned_data.get("course")
            if role == RoleAssignment.Role.COURSE_REP:
                assign_role(user=member, role=role, course=course, granted_by=request.user)
            else:
                assign_role(user=member, role=role, department=dept, granted_by=request.user)
            messages.success(request, "Role assigned.")
        elif action == "revoke_role":
            ra = get_object_or_404(RoleAssignment, pk=request.POST.get("assignment"), user=member)
            if ra.role == RoleAssignment.Role.SUPER_ADMIN and not request.user.is_platform_admin:
                raise PermissionDenied("Only super admins can change super admins.")
            revoke_role(user=member, role=ra.role, department=ra.department, course=ra.course, revoked_by=request.user)
            messages.success(request, "Role removed.")
        elif action == "verify_email":
            member.email_verified = True
            member.save(update_fields=["email_verified"])
            audit(request.user, "user.email_verified_manually", member)
            messages.success(request, "Email marked as verified.")
        elif action == "suspend":
            moderation.suspend_user(moderator=request.user, user=member, days=int(request.POST.get("days", 7)),
                                    note=request.POST.get("note", ""))
            messages.success(request, "Account suspended.")
        elif action == "unsuspend":
            moderation.unsuspend_user(moderator=request.user, user=member)
            messages.success(request, "Suspension lifted.")
        elif action == "ban":
            moderation.ban_user(moderator=request.user, user=member, note=request.POST.get("note", ""))
            messages.success(request, "Account banned.")
        elif action == "unban":
            member.is_active = True
            member.save(update_fields=["is_active"])
            moderation._log(request.user, dept.pk, moderation.ModerationAction.Action.UNBAN, "user", member)
            audit(request.user, "user.unbanned", member)
            messages.success(request, "Account restored.")
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, _err(exc) or "You can't do that.")
    return redirect("manage:user", pk=member.pk)


# --- Courses, sessions, Spaces --------------------------------------------------------
class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "title", "units", "level", "semester", "lecturer", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


@admin_required
def courses_view(request):
    dept = request.manage_department
    courses = Course.objects.filter(department=dept).select_related("space").annotate(
        reps=Count("role_assignments", filter=Q(role_assignments__role="course_rep")))
    return _render(request, "manage/courses.html", {"courses": courses}, "courses")


@admin_required
def course_form_view(request, pk=None):
    dept = request.manage_department
    course = get_object_or_404(Course, pk=pk, department=dept) if pk else Course(department=dept)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        code = " ".join(form.cleaned_data["code"].upper().split())
        if Course.objects.filter(department=dept, code=code).exclude(pk=course.pk).exists():
            form.add_error("code", "A course with this code already exists.")
        else:
            saved = form.save()
            audit(request.user, "course.saved", saved)
            messages.success(request, f"{saved.code} saved.{'' if pk else ' Its Space was created automatically.'}")
            return redirect("manage:courses")
    return _render(request, "manage/form.html", {"form": form, "heading": "Edit course" if pk else "Add course",
                                                 "back": "manage:courses"}, "courses")


class SessionForm(forms.ModelForm):
    class Meta:
        model = AcademicSession
        fields = ["name", "starts_on", "is_current"]
        widgets = {"starts_on": forms.DateInput(attrs={"type": "date"})}


@admin_required
def sessions_view(request):
    uni = request.manage_department.faculty.university
    form = SessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if AcademicSession.objects.filter(university=uni, name=form.cleaned_data["name"]).exists():
            form.add_error("name", "That session already exists.")
        else:
            s = form.save(commit=False)
            s.university = uni
            s.save()
            if s.is_current:
                AcademicSession.objects.filter(university=uni).exclude(pk=s.pk).update(is_current=False)
            messages.success(request, f"Session {s.name} added.")
            return redirect("manage:sessions")
    return _render(request, "manage/sessions.html", {"form": form,
                                                     "sessions": AcademicSession.objects.filter(university=uni)},
                   "sessions")


@admin_required
@require_POST
def session_current_view(request, pk):
    uni = request.manage_department.faculty.university
    session = get_object_or_404(AcademicSession, pk=pk, university=uni)
    AcademicSession.objects.filter(university=uni).update(is_current=False)
    session.is_current = True
    session.save(update_fields=["is_current"])
    messages.success(request, f"{session.name} is now the current session.")
    return redirect("manage:sessions")


class SpaceForm(forms.ModelForm):
    class Meta:
        model = Space
        fields = ["name", "icon", "kind", "level", "description", "rules", "is_official"]
        widgets = {"rules": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(v, l) for v, l in Space.Kind.choices if v != Space.Kind.COURSE]
        self.fields["level"] = forms.TypedChoiceField(choices=[("", "Not a level Space"), *Level.choices],
                                                      coerce=int, required=False, empty_value=None)


@admin_required
def spaces_view(request):
    spaces = Space.objects.filter(department=request.manage_department).select_related("course").order_by("kind", "name")
    return _render(request, "manage/spaces.html", {"spaces": spaces}, "spaces")


@admin_required
def space_form_view(request, pk=None):
    dept = request.manage_department
    space = get_object_or_404(Space, pk=pk, department=dept) if pk else Space(department=dept, created_by=request.user)
    if space.course_id:
        messages.info(request, "Course Spaces are managed through their course.")
        return redirect("manage:spaces")
    form = SpaceForm(request.POST or None, instance=space)
    if request.method == "POST" and form.is_valid():
        s = form.save(commit=False)
        if not s.slug:
            base = slugify(s.name)[:50] or "space"
            slug, n = base, 1
            while Space.objects.filter(department=dept, slug=slug).exists():
                n += 1
                slug = f"{base}-{n}"
            s.slug = slug
        s.save()
        audit(request.user, "space.saved", s)
        messages.success(request, f"{s.name} saved.")
        return redirect("manage:spaces")
    return _render(request, "manage/form.html", {"form": form, "heading": "Edit Space" if pk else "New Space",
                                                 "back": "manage:spaces"}, "spaces")


@admin_required
@require_POST
def space_delete_view(request, pk):
    space = get_object_or_404(Space, pk=pk, department=request.manage_department)
    try:
        moderation.remove_content(moderator=request.user, target_type="space", target_id=space.pk,
                                  note="Deleted from admin dashboard")
        messages.success(request, "Space deleted.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _err(exc))
    return redirect("manage:spaces")


# --- Announcements ----------------------------------------------------------------------
@admin_required
def announcements_view(request):
    items = Announcement.objects.filter(department=request.manage_department).select_related(
        "created_by", "target_course", "target_space").order_by("-created_at")[:200]
    return _render(request, "manage/announcements.html", {"items": items}, "announcements")


@admin_required
@require_POST
def announcement_remove_view(request, pk):
    item = get_object_or_404(Announcement, pk=pk, department=request.manage_department)
    item.is_removed = not item.is_removed
    item.save(update_fields=["is_removed"])
    audit(request.user, "announcement.removed" if item.is_removed else "announcement.restored", item)
    messages.success(request, "Announcement removed." if item.is_removed else "Announcement restored.")
    return redirect("manage:announcements")


# --- Super admin: institutions & topics ---------------------------------------------------
class InstitutionForm(forms.Form):
    university = forms.CharField(max_length=200, help_text='e.g. "University of Medical Sciences, Ondo"')
    short_name = forms.CharField(max_length=30, help_text="e.g. UNIMED")
    faculty = forms.CharField(max_length=200)
    department = forms.CharField(max_length=200)
    code = forms.CharField(max_length=10, help_text="e.g. CSC")


@super_required
def departments_view(request):
    form = InstitutionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        uni, _ = University.objects.get_or_create(slug=slugify(d["short_name"]),
                                                  defaults={"name": d["university"], "short_name": d["short_name"]})
        faculty, _ = Faculty.objects.get_or_create(university=uni, slug=slugify(d["faculty"]), defaults={"name": d["faculty"]})
        dept, created = Department.objects.get_or_create(faculty=faculty, slug=slugify(d["department"]),
                                                         defaults={"name": d["department"], "code": d["code"].upper()})
        audit(request.user, "department.created" if created else "department.exists", dept)
        messages.success(request, f"{dept.name} {'created' if created else 'already exists'}.")
        return redirect("manage:departments")
    depts = Department.objects.select_related("faculty__university").annotate(n=Count("members"))
    return _render(request, "manage/departments.html", {"form": form, "depts": depts}, "departments")


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ["name", "slug", "description", "is_active", "sort_order"]


@super_required
def topics_view(request):
    form = TopicForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Topic added.")
        return redirect("manage:topics")
    return _render(request, "manage/topics.html", {"form": form, "topics": Topic.objects.annotate(n=Count("posts"))},
                   "topics")


@super_required
@require_POST
def topic_toggle_view(request, pk):
    topic = get_object_or_404(Topic, pk=pk)
    topic.is_active = not topic.is_active
    topic.save(update_fields=["is_active"])
    return redirect("manage:topics")

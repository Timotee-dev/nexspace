from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import Department, Level
from apps.core.uploads import avatar_upload_path, validate_image_upload

USERNAME_REGEX = r"^[a-z0-9_]{3,30}$"
RESERVED_USERNAMES = {
    "admin", "administrator", "api", "settings", "login", "logout", "signup", "support", "nexspace",
    "onboarding", "static", "media", "u", "explore", "spaces", "courses", "anonymous", "moderator", "system",
}


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        if not extra.get("username"):
            from .services import generate_username

            extra["username"] = generate_username(extra.get("full_name") or email.split("@")[0])
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, email_verified=True, onboarding_completed=True)
        extra.setdefault("full_name", "NexSpace Admin")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120)
    username = models.CharField(
        max_length=30,
        unique=True,
        validators=[RegexValidator(USERNAME_REGEX, "Use 3–30 lowercase letters, numbers or underscores.")],
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="members"
    )
    level = models.PositiveSmallIntegerField(choices=Level.choices, null=True, blank=True)
    # Optional, never a login credential. Blank is stored as NULL so the unique constraint
    # only applies to real values.
    matric_number = models.CharField(max_length=30, unique=True, null=True, blank=True)

    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    onboarding_completed = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    suspended_until = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        indexes = [models.Index(fields=["department", "level"])]

    def __str__(self):
        return f"{self.full_name} (@{self.username})"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        self.username = (self.username or "").strip().lower()
        self.matric_number = (self.matric_number or "").strip().upper() or None
        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.username

    # --- Write access ------------------------------------------------------
    @property
    def is_suspended(self) -> bool:
        return bool(self.suspended_until and self.suspended_until > timezone.now())

    @property
    def can_write(self) -> bool:
        """Verified, active and not suspended: allowed to post, comment, vote, upload, rate and report."""
        return self.is_active and self.email_verified and not self.is_suspended

    # --- Roles -------------------------------------------------------------
    def has_role(self, role: str, department=None, course=None) -> bool:
        """True if the user holds `role` for the given scope.

        Super admins hold every role everywhere. Department admins act as course reps and
        moderators for their department. A course-scoped assignment only counts for that
        course; a department-scoped one for that department; an unscoped one everywhere.
        """
        if not self.is_active:
            return False
        if self.is_superuser:
            return True
        assignments = self.role_assignments.all()
        if assignments.filter(role=RoleAssignment.Role.SUPER_ADMIN).exists():
            return True
        if course is not None:
            department = course.department
        roles = {role}
        if role in (RoleAssignment.Role.COURSE_REP, RoleAssignment.Role.MODERATOR):
            roles.add(RoleAssignment.Role.DEPARTMENT_ADMIN)
        assignments = assignments.filter(role__in=roles)
        if department is None and course is None:
            return assignments.exists()
        scope = Q(department=department, course__isnull=True) | Q(department__isnull=True, course__isnull=True)
        if course is not None:
            scope |= Q(course=course)
        return assignments.filter(scope).exists()

    def can_moderate(self, department) -> bool:
        return self.has_role(RoleAssignment.Role.MODERATOR, department=department)

    @property
    def is_platform_admin(self) -> bool:
        return self.has_role(RoleAssignment.Role.SUPER_ADMIN)

    @property
    def level_label(self) -> str:
        return self.get_level_display() if self.level else ""


class Profile(models.Model):
    class Visibility(models.TextChoices):
        EVERYONE = "everyone", "Everyone on NexSpace"
        DEPARTMENT = "department", "Only my department"

    class Theme(models.TextChoices):
        SYSTEM = "system", "Match my device"
        DARK = "dark", "Dark"
        LIGHT = "light", "Light"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to=avatar_upload_path, blank=True, validators=[validate_image_upload])
    bio = models.CharField(max_length=280, blank=True)
    skills = models.JSONField(default=list, blank=True)
    github_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True)
    interests = models.ManyToManyField("topics.Topic", blank=True, related_name="interested_profiles")

    # Cached total of the NexScore ledger (ledger arrives in Phase 2).
    nexscore = models.IntegerField(default=0)

    visibility = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.EVERYONE)
    show_matric_number = models.BooleanField(default=False)
    show_social_links = models.BooleanField(default=True)
    show_joined_spaces = models.BooleanField(default=True)
    theme = models.CharField(max_length=8, choices=Theme.choices, default=Theme.SYSTEM)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of @{self.user.username}"

    def can_be_viewed_by(self, viewer) -> bool:
        if viewer.is_authenticated and (viewer.pk == self.user_id or viewer.is_staff or viewer.is_platform_admin):
            return True
        if not viewer.is_authenticated:
            return False
        if self.visibility == self.Visibility.DEPARTMENT:
            return viewer.department_id is not None and viewer.department_id == self.user.department_id
        return True

    @property
    def social_links(self) -> list[tuple[str, str]]:
        links = [("GitHub", self.github_url), ("LinkedIn", self.linkedin_url), ("Portfolio", self.portfolio_url)]
        return [(label, url) for label, url in links if url]

    def completion_steps(self) -> list[dict]:
        return [
            {"key": "avatar", "label": "Add a profile photo", "done": bool(self.avatar)},
            {"key": "bio", "label": "Write a short bio", "done": bool(self.bio)},
            {"key": "skills", "label": "List a few skills", "done": bool(self.skills)},
            {"key": "links", "label": "Link GitHub, LinkedIn or a portfolio", "done": bool(self.social_links)},
            {"key": "interests", "label": "Pick your interests", "done": self.interests.exists()},
        ]


class RoleAssignment(models.Model):
    """A role held by a user, optionally scoped to a department.

    Every user is implicitly a Student. Course-scoped roles (course reps) gain a
    `course` scope in Phase 3; Space-scoped moderation gains a `space` scope then too.
    """

    class Role(models.TextChoices):
        COURSE_REP = "course_rep", "Course Rep"
        MODERATOR = "moderator", "Moderator"
        DEPARTMENT_ADMIN = "department_admin", "Department Admin"
        SUPER_ADMIN = "super_admin", "Super Admin"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.CharField(max_length=20, choices=Role.choices)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, null=True, blank=True, related_name="role_assignments"
    )
    course = models.ForeignKey(
        "academics.Course", on_delete=models.CASCADE, null=True, blank=True, related_name="role_assignments",
        help_text="Course reps: the course they represent (leave department empty)",
    )
    granted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="roles_granted"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "department"],
                condition=Q(department__isnull=False),
                name="unique_scoped_role",
            ),
            models.UniqueConstraint(
                fields=["user", "role", "course"], condition=Q(course__isnull=False), name="unique_course_role"
            ),
            models.UniqueConstraint(
                fields=["user", "role"], condition=Q(department__isnull=True, course__isnull=True),
                name="unique_unscoped_role",
            ),
        ]

    def __str__(self):
        scope = self.course.code if self.course_id else (self.department.name if self.department else "all departments")
        return f"{self.user.username}: {self.get_role_display()} ({scope})"

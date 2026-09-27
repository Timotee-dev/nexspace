from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.uploads import post_file_upload_path

BODY_MAX = 2000
COMMENT_MAX = 1000
MAX_TOPICS = 3
MAX_COMMENT_DEPTH = 3


class PostQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(is_deleted=False, is_hidden=False)

    def for_viewer(self, user):
        """Posts a user may see: live posts in their own department."""
        return self.visible().filter(department_id=user.department_id)

    def with_related(self):
        return self.select_related(
            "author__profile", "poll", "event", "opportunity", "department", "space__course"
        ).prefetch_related("topics", "attachments", "poll__options")


class Post(models.Model):
    class Kind(models.TextChoices):
        POST = "post", "Post"
        QUESTION = "question", "Question"
        POLL = "poll", "Poll"
        EVENT = "event", "Event"
        OPPORTUNITY = "opportunity", "Opportunity"

    ANONYMOUS_KINDS = {Kind.POST, Kind.QUESTION}

    # The real author is always stored (moderation needs it) but never shown for anonymous posts.
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    department = models.ForeignKey("academics.Department", on_delete=models.CASCADE, related_name="posts")
    space = models.ForeignKey("spaces.Space", on_delete=models.SET_NULL, null=True, blank=True, related_name="posts")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.POST)
    title = models.CharField(max_length=150, blank=True)
    body = models.TextField(max_length=BODY_MAX, blank=True)
    is_anonymous = models.BooleanField(default=False)
    is_official = models.BooleanField(default=False, help_text="Official department update")
    topics = models.ManyToManyField("topics.Topic", blank=True, related_name="posts")
    mentions = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="mentioned_in_posts")

    score = models.IntegerField(default=0)
    comment_count = models.PositiveIntegerField(default=0)
    accepted_comment = models.ForeignKey(
        "Comment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # Hidden automatically after enough reports, until a moderator reviews it.
    is_hidden = models.BooleanField(default=False)
    removed_by_moderator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["department", "is_deleted", "-created_at"]),
            models.Index(fields=["author", "-created_at"]),
            models.Index(fields=["department", "is_official", "-created_at"]),
            models.Index(fields=["space", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(is_anonymous=False) | Q(kind__in=["post", "question"]),
                name="anonymous_only_posts_and_questions",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("posts:detail", args=[self.pk])

    @property
    def display_name(self):
        return "Anonymous Student" if self.is_anonymous else self.author.full_name

    @property
    def images(self):
        return [a for a in self.attachments.all() if a.kind == Attachment.Kind.IMAGE]

    @property
    def files(self):
        return [a for a in self.attachments.all() if a.kind == Attachment.Kind.FILE]


class EventDetails(models.Model):
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name="event")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=150, blank=True)


class OpportunityDetails(models.Model):
    class Category(models.TextChoices):
        INTERNSHIP = "internship", "Internship"
        SIWES = "siwes", "SIWES"
        SCHOLARSHIP = "scholarship", "Scholarship"
        JOB = "job", "Job"
        HACKATHON = "hackathon", "Hackathon"
        COMPETITION = "competition", "Competition"
        FELLOWSHIP = "fellowship", "Fellowship"
        CONFERENCE = "conference", "Conference"
        TECH_EVENT = "tech_event", "Tech event"

    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name="opportunity")
    organization = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices)
    deadline = models.DateField(null=True, blank=True)
    apply_url = models.URLField(blank=True)
    location = models.CharField(max_length=120, blank=True, help_text="City, or 'Remote'")


class Poll(models.Model):
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name="poll")
    allows_multiple = models.BooleanField(default=False)
    closes_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_closed(self):
        from django.utils import timezone

        return self.closes_at is not None and self.closes_at <= timezone.now()

    @property
    def total_votes(self):
        return sum(o.vote_count for o in self.options.all())

    @property
    def voter_count(self):
        return self.votes.values("user").distinct().count()


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=80)
    position = models.PositiveSmallIntegerField(default=0)
    vote_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]


class PollVote(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes")
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="poll_votes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["option", "user"], name="unique_poll_option_vote")]
        indexes = [models.Index(fields=["poll", "user"])]


class Attachment(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        FILE = "file", "File"

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    file = models.FileField(upload_to=post_file_upload_path)
    original_name = models.CharField(max_length=150)
    size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].upper() if "." in self.original_name else "FILE"


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    depth = models.PositiveSmallIntegerField(default=1)
    reply_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Set when a reply is flattened into a depth-3 thread",
    )
    body = models.TextField(max_length=COMMENT_MAX)
    mentions = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="mentioned_in_comments")
    score = models.IntegerField(default=0)
    is_deleted = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    removed_by_moderator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "created_at"]), models.Index(fields=["author", "-created_at"])]

    def __str__(self):
        return f"Comment #{self.pk} on post #{self.post_id}"


class PostVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_votes")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="votes")
    value = models.SmallIntegerField(choices=[(1, "Up"), (-1, "Down")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="unique_post_vote")]


class CommentVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comment_votes")
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="votes")
    value = models.SmallIntegerField(choices=[(1, "Up"), (-1, "Down")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "comment"], name="unique_comment_vote")]


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="bookmarks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="unique_bookmark")]

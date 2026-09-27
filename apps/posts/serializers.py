from rest_framework import serializers

from .models import Comment, Post


def public_author(user, request):
    avatar = user.profile.avatar
    return {
        "display_name": user.full_name,
        "username": user.username,
        "avatar_url": request.build_absolute_uri(avatar.url) if avatar else None,
        "level": user.level_label,
    }


ANONYMOUS_AUTHOR = {"display_name": "Anonymous Student", "username": None, "avatar_url": None, "level": None}


class PollOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    vote_count = serializers.IntegerField()


class PostSerializer(serializers.ModelSerializer):
    """Never exposes the author of an anonymous post — no id, username or name."""

    author = serializers.SerializerMethodField()
    topics = serializers.SlugRelatedField(many=True, read_only=True, slug_field="slug")
    space = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    my_vote = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    poll = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    opportunity = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "kind", "title", "body", "author", "is_anonymous", "is_official", "topics", "score",
            "comment_count", "my_vote", "is_bookmarked", "is_mine", "accepted_comment_id", "created_at", "space",
            "url", "attachments", "poll", "event", "opportunity",
        ]

    def get_author(self, obj) -> dict:
        return ANONYMOUS_AUTHOR if obj.is_anonymous else public_author(obj.author, self.context["request"])

    def get_my_vote(self, obj) -> int:
        if getattr(obj, "my_vote_up", False):
            return 1
        return -1 if getattr(obj, "my_vote_down", False) else 0

    def get_is_bookmarked(self, obj) -> bool:
        return bool(getattr(obj, "is_bookmarked", False))

    def get_is_mine(self, obj) -> bool:
        return obj.author_id == self.context["request"].user.pk

    def get_url(self, obj) -> str:
        return self.context["request"].build_absolute_uri(obj.get_absolute_url())

    def get_attachments(self, obj) -> list:
        from django.urls import reverse

        request = self.context["request"]
        return [
            {"id": a.id, "kind": a.kind, "name": a.original_name, "size": a.size,
             "url": request.build_absolute_uri(reverse("posts:attachment", args=[obj.pk, a.pk]))}
            for a in obj.attachments.all()
        ]

    def get_poll(self, obj) -> dict | None:
        poll = getattr(obj, "poll", None) if obj.kind == Post.Kind.POLL else None
        if poll is None:
            return None
        show = getattr(obj, "has_voted_poll", False) or poll.is_closed or obj.author_id == self.context["request"].user.pk
        options = [{"id": o.id, "text": o.text, "vote_count": o.vote_count if show else None} for o in poll.options.all()]
        return {"allows_multiple": poll.allows_multiple, "closes_at": poll.closes_at, "is_closed": poll.is_closed,
                "has_voted": bool(getattr(obj, "has_voted_poll", False)), "results_visible": show,
                "total_votes": poll.total_votes if show else None, "options": options}

    def get_event(self, obj) -> dict | None:
        event = getattr(obj, "event", None) if obj.kind == Post.Kind.EVENT else None
        return None if event is None else {"starts_at": event.starts_at, "ends_at": event.ends_at, "location": event.location}

    def get_opportunity(self, obj) -> dict | None:
        opp = getattr(obj, "opportunity", None) if obj.kind == Post.Kind.OPPORTUNITY else None
        if opp is None:
            return None
        return {"organization": opp.organization, "category": opp.category, "deadline": opp.deadline,
                "apply_url": opp.apply_url, "location": opp.location}


class PostCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Post.Kind.choices)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    body = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    topics = serializers.ListField(child=serializers.SlugField(), required=False, max_length=3)
    is_anonymous = serializers.BooleanField(required=False, default=False)
    is_official = serializers.BooleanField(required=False, default=False)
    poll_options = serializers.ListField(child=serializers.CharField(max_length=80), required=False, max_length=6)
    poll_multiple = serializers.BooleanField(required=False, default=False)
    poll_closes_at = serializers.DateTimeField(required=False, allow_null=True)
    event = serializers.DictField(required=False)
    opportunity = serializers.DictField(required=False)
    space = serializers.SlugField(required=False, help_text="Slug of a Space you've joined")


class CommentSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    my_vote = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "parent_id", "depth", "author", "reply_to", "body", "score", "my_vote",
                  "is_deleted", "is_mine", "created_at"]

    def get_author(self, obj) -> dict | None:
        return None if obj.is_deleted else public_author(obj.author, self.context["request"])

    def get_body(self, obj) -> str:
        return "" if obj.is_deleted else obj.body

    def get_reply_to(self, obj) -> str | None:
        return obj.reply_to.username if obj.reply_to_id else None

    def get_my_vote(self, obj) -> int:
        if getattr(obj, "my_vote_up", False):
            return 1
        return -1 if getattr(obj, "my_vote_down", False) else 0

    def get_is_mine(self, obj) -> bool:
        return obj.author_id == self.context["request"].user.pk


class CommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=1000)
    parent = serializers.IntegerField(required=False, allow_null=True)


class VoteSerializer(serializers.Serializer):
    value = serializers.ChoiceField(choices=[1, -1, 0])


class PollVoteSerializer(serializers.Serializer):
    options = serializers.ListField(child=serializers.IntegerField(), min_length=1)

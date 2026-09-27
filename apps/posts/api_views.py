from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.social import services as social
from apps.topics.models import Topic

from . import feed, services
from .models import Bookmark, Comment, Post
from .serializers import (
    CommentCreateSerializer, CommentSerializer, PollVoteSerializer, PostCreateSerializer, PostSerializer,
    VoteSerializer,
)
from .views import build_thread


def run(fn, **kwargs):
    """Call a service and translate its exceptions into API errors."""
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        raise exceptions.ValidationError({"non_field_errors": exc.messages})
    except DjangoPermissionDenied as exc:
        raise exceptions.PermissionDenied(str(exc) or None)
    except services.RateLimited as exc:
        raise exceptions.Throttled(detail=str(exc))


def visible_post(request, pk):
    post = get_object_or_404(feed.annotate_for_user(Post.objects.with_related(), request.user), pk=pk)
    if not services.can_view(request.user, post):
        raise Http404
    return post


class PostListCreateAPIView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        parameters=[
            OpenApiParameter("tab", str, enum=list(feed.TABS)),
            OpenApiParameter("page", int, description="For You only"),
            OpenApiParameter("before", int, description="Following/Department: id cursor"),
        ],
        responses=PostSerializer(many=True),
    )
    def get(self, request):
        tab = request.query_params.get("tab", "for-you")
        if tab not in feed.TABS:
            raise exceptions.ValidationError({"tab": f"Use one of: {', '.join(feed.TABS)}"})
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
            before = int(request.query_params["before"]) if request.query_params.get("before") else None
        except ValueError:
            raise exceptions.ValidationError({"page": "Must be a number."})
        posts, next_params = feed.get_feed(request.user, tab, page=page, before=before)
        return Response({"results": PostSerializer(posts, many=True, context={"request": request}).data,
                         "next": next_params})

    @extend_schema(request=PostCreateSerializer, responses={201: PostSerializer})
    def post(self, request):
        serializer = PostCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["topics"] = list(Topic.objects.filter(slug__in=data.get("topics", []), is_active=True))
        if data.get("space"):
            from apps.spaces.models import Space

            data["space"] = get_object_or_404(Space, slug=data["space"], department_id=request.user.department_id)
        post = run(
            services.create_post, author=request.user,
            images=request.FILES.getlist("images"), files=request.FILES.getlist("files"), **data,
        )
        post = visible_post(request, post.pk)
        return Response(PostSerializer(post, context={"request": request}).data, status=status.HTTP_201_CREATED)


class PostDetailAPIView(APIView):
    @extend_schema(responses=PostSerializer)
    def get(self, request, pk):
        return Response(PostSerializer(visible_post(request, pk), context={"request": request}).data)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        run(services.delete_post, user=request.user, post=visible_post(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PostVoteAPIView(APIView):
    @extend_schema(request=VoteSerializer, responses={200: dict})
    def post(self, request, pk):
        serializer = VoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        score, mine = run(services.vote_post, user=request.user, post=visible_post(request, pk),
                          value=serializer.validated_data["value"])
        return Response({"score": score, "my_vote": mine})


class PostBookmarkAPIView(APIView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request, pk):
        saved = run(services.toggle_bookmark, user=request.user, post=visible_post(request, pk))
        return Response({"bookmarked": saved})


class PollVoteAPIView(APIView):
    @extend_schema(request=PollVoteSerializer, responses=PostSerializer)
    def post(self, request, pk):
        serializer = PollVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run(services.vote_poll, user=request.user, post=visible_post(request, pk),
            option_ids=serializer.validated_data["options"])
        return Response(PostSerializer(visible_post(request, pk), context={"request": request}).data)


class CommentListCreateAPIView(APIView):
    @extend_schema(responses=CommentSerializer(many=True))
    def get(self, request, pk):
        post = visible_post(request, pk)
        flat = []

        def walk(nodes):
            for node in nodes:
                flat.append(node)
                walk(node.children)

        walk(build_thread(post, request.user))
        return Response(CommentSerializer(flat, many=True, context={"request": request}).data)

    @extend_schema(request=CommentCreateSerializer, responses={201: CommentSerializer})
    def post(self, request, pk):
        post = visible_post(request, pk)
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent_id = serializer.validated_data.get("parent")
        parent = get_object_or_404(Comment, pk=parent_id, post=post) if parent_id else None
        comment = run(services.add_comment, user=request.user, post=post,
                      body=serializer.validated_data["body"], parent=parent)
        return Response(CommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED)


def visible_comment(request, pk):
    comment = get_object_or_404(Comment.objects.select_related("post", "author__profile"), pk=pk)
    if not services.can_view(request.user, comment.post):
        raise Http404
    return comment


class CommentDetailAPIView(APIView):
    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        run(services.delete_comment, user=request.user, comment=visible_comment(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CommentVoteAPIView(APIView):
    @extend_schema(request=VoteSerializer, responses={200: dict})
    def post(self, request, pk):
        serializer = VoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        score, mine = run(services.vote_comment, user=request.user, comment=visible_comment(request, pk),
                          value=serializer.validated_data["value"])
        return Response({"score": score, "my_vote": mine})


class AcceptAnswerAPIView(APIView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request, pk):
        accepted = run(services.accept_answer, user=request.user, comment=visible_comment(request, pk))
        return Response({"accepted_comment_id": accepted.pk if accepted else None})


class BookmarkListAPIView(APIView):
    @extend_schema(responses=PostSerializer(many=True))
    def get(self, request):
        ids = list(Bookmark.objects.filter(user=request.user, post__is_deleted=False)
                   .values_list("post_id", flat=True)[:200])
        posts = {p.pk: p for p in feed.annotate_for_user(
            Post.objects.for_viewer(request.user).with_related(), request.user).filter(pk__in=ids)}
        ordered = [posts[i] for i in ids if i in posts]
        return Response(PostSerializer(ordered, many=True, context={"request": request}).data)


class UserFollowAPIView(APIView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request, username):
        target = get_object_or_404(User, username=username.lower(), is_active=True)
        if not target.profile.can_be_viewed_by(request.user):
            raise Http404
        try:
            following = social.toggle_user_follow(request.user, target)
        except ValueError as exc:
            raise exceptions.ValidationError({"non_field_errors": [str(exc)]})
        return Response({"following": following, "follower_count": target.follower_set.count()})


class TopicFollowAPIView(APIView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request, slug):
        topic = get_object_or_404(Topic, slug=slug, is_active=True)
        following = social.toggle_topic_follow(request.user, topic)
        return Response({"following": following, "follower_count": topic.followers.count()})

"""API for the NexSpace Android app (`/api/m/...`).

Signs in with a device token (see authentication.py) and reuses the same services as the website, so every
permission, privacy rule and rate limit applies exactly as it does on the web. Endpoints that already exist
under /api/ (feed, posts, comments, votes, notifications, search, profiles) are used by the app directly.
"""
from django.contrib.auth import authenticate
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView as _APIView


class APIView(_APIView):
    schema = None  # the app's endpoints are private to the app, so they stay out of the public API docs

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.accounts.serializers import MeSerializer, SignupSerializer
from apps.core import ratelimit
from apps.core.throttles import AuthRateThrottle
from apps.posts.services import RateLimited

from .models import DeviceToken, hash_key

DOWNLOAD_SALT = "nexspace.mobile.download"
DOWNLOAD_MAX_AGE = 60 * 10  # signed download links work for 10 minutes


def _error(code, message, http_status=400):
    return Response({"error": {"code": code, "message": message}}, status=http_status)


def run(fn, **kwargs):
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        raise exceptions.ValidationError({"non_field_errors": exc.messages})
    except DjangoPermissionDenied as exc:
        raise exceptions.PermissionDenied(str(exc) or None)
    except RateLimited as exc:
        raise exceptions.Throttled(detail=str(exc))


def absolute(request, url):
    if not url:
        return None
    return url if url.startswith("http") else request.build_absolute_uri(url)


def person(request, user):
    if user is None or not user.is_active:
        return {"name": "Deleted user", "username": None, "avatar_url": None, "badge": ""}
    avatar = user.profile.avatar
    return {"name": user.display_name, "username": user.username,
            "avatar_url": absolute(request, avatar.url) if avatar else None, "badge": user.staff_badge}


def me_payload(request, user):
    data = MeSerializer(user).data
    data["avatar_url"] = absolute(request, data.get("avatar_url"))
    data["display_name"] = user.display_name
    data["staff_badge"] = user.staff_badge
    return data


# --- Signing in ------------------------------------------------------------------------
class LoginAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        if not email or not password:
            return _error("missing", "Enter your email and password.")
        if ratelimit.is_locked(request, email):
            return _error("locked", "Too many failed attempts. Try again in 15 minutes, or reset your password.", 429)
        user = authenticate(request, email=email, password=password)
        if user is None:
            ratelimit.record_failure(request, email)
            return _error("invalid_credentials", "That email and password don't match an account.")
        ratelimit.clear_failures(request, email)
        from apps.accounts.owner import demote_if_not_owner, ensure_owner

        ensure_owner(user)
        demote_if_not_owner(user)
        token = DeviceToken.issue(user, request.data.get("device", ""))
        return Response({"token": token, "me": me_payload(request, user)})


def _join_starter_spaces(user):
    """What the website's onboarding does by default: join your level's courses, ask to join the rest."""
    from django.db.models import Q

    from apps.spaces import services as spaces
    from apps.spaces.models import Space

    starters = Space.objects.filter(department_id=user.department_id).filter(
        Q(kind="department") | Q(kind="level", level=user.level) | Q(course__level=user.level, course__is_active=True))
    for space in starters:
        try:
            spaces.request_to_join(user, space)
        except Exception:  # e.g. not verified yet: the app asks again later
            continue


class SignupAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            user = accounts.register_user(
                email=data["email"], password=data["password"], full_name=data["full_name"],
                department=data["department"], level=data["level"], matric_number=data.get("matric_number"),
            )
        accounts.send_verification_email(user)
        token = DeviceToken.issue(user, request.data.get("device", ""))
        return Response({"token": token, "me": me_payload(request, user)}, status=status.HTTP_201_CREATED)


class LogoutAPIView(APIView):
    def post(self, request):
        if isinstance(request.auth, DeviceToken):
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeAPIView(APIView):
    def get(self, request):
        user = User.objects.select_related("profile", "department").get(pk=request.user.pk)
        return Response(me_payload(request, user))


class VerifyCodeAPIView(APIView):
    def post(self, request):
        user = request.user
        if user.email_verified:
            return Response({"me": me_payload(request, user)})
        if not accounts.check_code(user, "verify", request.data.get("code", "")):
            return _error("wrong_code", "That code isn't right, or it has expired. Use the newest email, or send a "
                                        "new code. After 5 wrong tries you'll need a new code.")
        accounts.mark_verified(user)
        user.refresh_from_db()
        if not user.onboarding_completed:
            _join_starter_spaces(user)
            User.objects.filter(pk=user.pk).update(onboarding_completed=True)
            user.onboarding_completed = True
        return Response({"me": me_payload(request, user)})


class ResendCodeAPIView(APIView):
    def post(self, request):
        if request.user.email_verified:
            return _error("already_verified", "Your email is already verified.")
        if not cache.add(f"resend-verify:{request.user.pk}", 1, 60):
            return _error("throttled", "A code was sent less than a minute ago. Check your inbox and spam folder.", 429)
        accounts.send_verification_email(request.user)
        return Response({"detail": f"We sent a new code to {request.user.email}."})


class PasswordResetAPIView(APIView):
    """Sends the same reset email as the website; the link opens in the phone's browser."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.contrib.auth.forms import PasswordResetForm

        form = PasswordResetForm({"email": (request.data.get("email") or "").strip().lower()})
        if form.is_valid():
            form.save(request=request, use_https=request.is_secure(),
                      subject_template_name="emails/password_reset_subject.txt",
                      email_template_name="emails/password_reset.txt",
                      html_email_template_name="emails/password_reset.html")
        return Response({"detail": "If that email has an account, we've sent a reset link."})


class DepartmentsAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from apps.academics.models import Department, Level

        depts = Department.objects.filter(is_active=True).select_related("faculty__university").order_by("name")
        return Response({
            "departments": [{"id": d.pk, "name": d.name, "university": d.faculty.university.short_name} for d in depts],
            "levels": [{"value": v, "label": label} for v, label in Level.choices],
        })


# --- Counts for badges -------------------------------------------------------------------
class CountsAPIView(APIView):
    def get(self, request):
        from apps.messaging.services import unread_count as unread_messages
        from apps.notifications.services import unread_count

        return Response({"notifications": unread_count(request.user), "messages": unread_messages(request.user)})


# --- Signed downloads ----------------------------------------------------------------------
def signed_download(request, kind, pk):
    token = signing.dumps({"k": kind, "id": pk, "u": request.user.pk}, salt=DOWNLOAD_SALT)
    return request.build_absolute_uri(reverse("mobile:file", args=[token]))


def file_view(request, token):
    """Opens a file from a short-lived signed link, so the phone's viewer can open it without the app token."""
    from apps.core.storage import download_url

    try:
        data = signing.loads(token, salt=DOWNLOAD_SALT, max_age=DOWNLOAD_MAX_AGE)
    except signing.BadSignature:
        raise Http404
    user = get_object_or_404(User, pk=data["u"], is_active=True)
    kind, pk = data["k"], data["id"]
    inline = False
    if kind == "resource":
        from apps.resources import services as resources
        from apps.resources.models import Resource

        item = get_object_or_404(Resource.objects.select_related("course"), pk=pk)
        if not resources.can_view(user, item):
            raise Http404
        resources.record_download(user=user, resource=item)
        field, name = item.file, item.original_name
    elif kind == "post-file":
        from apps.posts.models import Attachment
        from apps.posts.services import can_view

        item = get_object_or_404(Attachment.objects.select_related("post"), pk=pk)
        if not can_view(user, item.post):
            raise Http404
        field, name, inline = item.file, item.original_name, item.kind == "image"
    elif kind == "message-file":
        from apps.messaging.models import MessageAttachment

        item = get_object_or_404(MessageAttachment.objects.select_related("message__conversation"), pk=pk)
        msg = item.message
        if msg.is_deleted or msg.is_hidden or not msg.conversation.members.filter(user=user).exists():
            raise Http404
        field, name, inline = item.file, item.original_name, item.kind == "image"
    elif kind == "announcement":
        from apps.notices.models import Announcement

        item = get_object_or_404(Announcement, pk=pk, is_removed=False, department_id=user.department_id)
        if not item.attachment:
            raise Http404
        field, name = item.attachment, item.attachment_name
    else:
        raise Http404
    from django.core.files.storage import FileSystemStorage

    if isinstance(field.storage, FileSystemStorage):
        return FileResponse(field.open("rb"), as_attachment=not inline, filename=name)
    return HttpResponseRedirect(download_url(field, name, as_attachment=not inline))


# --- Courses and materials ----------------------------------------------------------------
def course_json(course, joined):
    return {"slug": course.slug, "code": course.code, "title": course.title, "level": course.level,
            "semester": course.semester, "units": course.units, "lecturer": course.lecturer, "joined": joined,
            "space_id": getattr(getattr(course, "space", None), "pk", None)}


def resource_json(request, r):
    return {"id": r.id, "title": r.title, "type": r.resource_type, "type_label": r.get_resource_type_display(),
            "exam_type": r.exam_type, "session": r.session.name if r.session_id else None,
            "course": r.course.code, "name": r.original_name, "size": r.size, "downloads": r.download_count,
            "rating": round(r.rating_sum / r.rating_count, 1) if r.rating_count else None,
            "verified": r.is_verified_upload, "created_at": r.created_at,
            "text_status": r.ocr_status, "download_url": signed_download(request, "resource", r.id)}


class CoursesAPIView(APIView):
    def get(self, request):
        from apps.academics.models import Course
        from apps.spaces.services import joined_course_ids

        mine = joined_course_ids(request.user)
        courses = (Course.objects.filter(department_id=request.user.department_id, is_active=True)
                   .select_related("space").order_by("level", "code"))
        return Response({"courses": [course_json(c, c.pk in mine) for c in courses]})


class CourseDetailAPIView(APIView):
    def get(self, request, slug):
        from apps.academics.models import Course
        from apps.notices.models import AcademicEvent
        from apps.resources.models import Resource
        from apps.spaces.services import joined_course_ids

        course = get_object_or_404(Course.objects.select_related("space"), slug=slug,
                                   department_id=request.user.department_id)
        resources = (Resource.objects.for_viewer(request.user).filter(course=course)
                     .select_related("course", "session").order_by("-created_at")[:100])
        events = (AcademicEvent.objects.filter(target_course=course, starts_at__gte=timezone.now())
                  .order_by("starts_at")[:5])
        return Response({
            "course": course_json(course, course.pk in joined_course_ids(request.user)),
            "description": course.description,
            "resources": [resource_json(request, r) for r in resources],
            "events": [event_json(e) for e in events],
        })


# --- Spaces ----------------------------------------------------------------------------------
class SpacesAPIView(APIView):
    def get(self, request):
        from apps.spaces.models import Space, SpaceMembership
        from apps.spaces.services import pending_request_ids

        mine = set(SpaceMembership.objects.filter(user=request.user).values_list("space_id", flat=True))
        pending = pending_request_ids(request.user)
        spaces = Space.objects.filter(department_id=request.user.department_id).order_by("kind", "name")
        return Response({"spaces": [{
            "id": s.pk, "slug": s.slug, "name": s.name, "icon": s.icon, "kind": s.kind,
            "description": s.description, "members": s.member_count, "private": s.requires_approval,
            "joined": s.pk in mine, "requested": s.pk in pending, "course": s.course.slug if s.course_id else None,
        } for s in spaces.select_related("course")]})


class SpaceJoinAPIView(APIView):
    def post(self, request, pk):
        from apps.spaces import services as spaces
        from apps.spaces.models import Space

        space = get_object_or_404(Space, pk=pk, department_id=request.user.department_id)
        if request.data.get("action") == "leave":
            run(spaces.leave, user=request.user, space=space)
            return Response({"joined": False, "requested": False})
        result = run(spaces.request_to_join, user=request.user, space=space, message=request.data.get("message", ""))
        return Response({"joined": result in ("joined", "member"), "requested": result in ("requested", "pending")})


class SpacePostsAPIView(APIView):
    def get(self, request, pk):
        from apps.posts import feed
        from apps.posts.models import Post
        from apps.posts.serializers import PostSerializer
        from apps.spaces.models import Space
        from apps.spaces.services import can_see_inside

        space = get_object_or_404(Space, pk=pk, department_id=request.user.department_id)
        if not can_see_inside(request.user, space):
            return Response({"private": True, "results": []})
        posts = feed.annotate_for_user(Post.objects.for_viewer(request.user).with_related()
                                       .filter(space=space).exclude(kind="repost").order_by("-created_at"),
                                       request.user)[:40]
        posts = feed.expand_reposts(list(posts), request.user)  # marks which ones you've reposted, in one query
        return Response({"private": False,
                         "results": PostSerializer(posts, many=True, context={"request": request}).data})


# --- Calendar and announcements --------------------------------------------------------------
def event_json(e):
    return {"id": e.pk, "title": e.title, "kind": e.kind, "kind_label": e.get_kind_display(),
            "starts_at": e.starts_at, "ends_at": e.ends_at, "all_day": e.all_day, "location": e.location,
            "description": e.description, "course": e.target_course.code if e.target_course_id else None}


class CalendarAPIView(APIView):
    def get(self, request):
        from apps.notices.services import upcoming_for

        return Response({"events": [event_json(e) for e in upcoming_for(request.user, limit=60)]})


class AnnouncementsAPIView(APIView):
    def get(self, request):
        from apps.notices.models import Announcement

        items = (Announcement.objects.active().relevant_to(request.user)
                 .select_related("created_by", "target_course").order_by("-created_at")[:50])
        return Response({"announcements": [{
            "id": a.pk, "title": a.title, "body": a.body, "priority": a.priority, "audience": a.audience_label,
            "created_at": a.created_at, "author": person(request, a.created_by),
            "attachment": {"name": a.attachment_name, "url": signed_download(request, "announcement", a.pk)}
            if a.attachment else None,
        } for a in items]})


# --- Messages ---------------------------------------------------------------------------------------
def message_json(request, m):
    gone = m.is_deleted or m.is_hidden
    return {
        "id": m.pk, "mine": m.sender_id == request.user.pk, "system": m.is_system, "deleted": gone,
        "sender": person(request, m.sender), "body": "" if gone else m.body, "created_at": m.created_at,
        "attachments": [] if gone else [{"id": a.pk, "kind": a.kind, "name": a.original_name, "size": a.size,
                                         "url": signed_download(request, "message-file", a.pk)}
                                        for a in m.attachments.all()],
    }


def _conversation(request, pk):
    from apps.messaging import services as dm
    from apps.messaging.models import Conversation

    conversation = get_object_or_404(Conversation, pk=pk)
    try:
        dm.membership(request.user, conversation)
    except DjangoPermissionDenied:
        raise Http404
    return conversation


class ConversationsAPIView(APIView):
    def get(self, request):
        from apps.messaging import services as dm

        rows = dm.inbox(request.user)
        return Response({"conversations": [{
            "id": r["conversation"].pk, "is_group": r["is_group"], "unread": r["unread"], "muted": r["muted"],
            "title": r["conversation"].title if r["is_group"] else (r["other"].display_name if r["other"] else "Deleted user"),
            "other": person(request, r["other"]) if r["other"] else None, "members": r["member_count"],
            "last": None if r["last"] is None else {
                "body": "" if (r["last"].is_deleted or r["last"].is_hidden) else (r["last"].body or "📎 Attachment"),
                "mine": r["last"].sender_id == request.user.pk, "system": r["last"].is_system,
                "deleted": r["last"].is_deleted or r["last"].is_hidden, "created_at": r["last"].created_at},
        } for r in rows]})


class StartConversationAPIView(APIView):
    def post(self, request, username):
        from apps.messaging import services as dm

        other = get_object_or_404(User, username=username.lower(), is_active=True)
        conversation = run(dm.start_conversation, sender=request.user, recipient=other)
        return Response({"id": conversation.pk})


class ConversationMessagesAPIView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        from apps.messaging import services as dm

        conversation = _conversation(request, pk)
        after = int(request.query_params.get("after") or 0)
        if after:
            items = dm.messages_after(conversation, after)
        else:
            items = list(conversation.messages.select_related("sender__profile").prefetch_related("attachments")
                         .order_by("-id")[:80])[::-1]
        if items:
            dm.mark_read(request.user, conversation)
        other = dm.other_member(conversation, request.user)
        can_send, reason = (True, "") if conversation.is_group else (
            dm.can_message(request.user, other) if other else (False, "This conversation has ended."))
        return Response({
            "conversation": {"id": conversation.pk, "is_group": conversation.is_group,
                             "title": conversation.title if conversation.is_group else (other.display_name if other else "Deleted user"),
                             "other": person(request, other) if other else None,
                             "members": conversation.members.count(), "can_send": can_send, "reason": reason},
            "messages": [message_json(request, m) for m in items],
        })

    def post(self, request, pk):
        from apps.messaging import services as dm

        conversation = _conversation(request, pk)
        message = run(dm.send_message, sender=request.user, conversation=conversation,
                      body=request.data.get("body", ""), files=request.FILES.getlist("files"))
        message = conversation.messages.select_related("sender__profile").prefetch_related("attachments").get(pk=message.pk)
        return Response({"message": message_json(request, message)}, status=status.HTTP_201_CREATED)


class DeleteMessageAPIView(APIView):
    def delete(self, request, pk, message_id):
        from apps.messaging import services as dm

        conversation = _conversation(request, pk)
        message = get_object_or_404(conversation.messages, pk=message_id)
        run(dm.delete_message, user=request.user, message=message)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Reports -----------------------------------------------------------------------------------------
class ReportAPIView(APIView):
    def post(self, request):
        from apps.moderation import services as moderation

        run(moderation.submit_report, reporter=request.user, target_type=request.data.get("target_type", ""),
            target_id=int(request.data.get("target_id") or 0), reason=request.data.get("reason", ""),
            details=request.data.get("details", ""))
        return Response({"detail": "Thanks. Moderators will review it."}, status=status.HTTP_201_CREATED)


class DevicesAPIView(APIView):
    """Signed-in phones (so a lost phone can be signed out)."""

    def get(self, request):
        current = request.auth.pk if isinstance(request.auth, DeviceToken) else None
        return Response({"devices": [{"id": t.pk, "name": t.name or "Phone", "created_at": t.created_at,
                                      "last_used_at": t.last_used_at, "current": t.pk == current}
                                     for t in request.user.device_tokens.all()]})

    def delete(self, request):
        keep = request.auth.pk if isinstance(request.auth, DeviceToken) else None
        request.user.device_tokens.exclude(pk=keep).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


__all__ = ["hash_key"]


class ProfileAPIView(APIView):
    """Someone's profile for the app: who they are, follow state and their recent posts."""

    def get(self, request, username):
        from apps.accounts.views import _can_message
        from apps.posts.feed import annotate_for_user, expand_reposts
        from apps.posts.models import Post
        from apps.posts.serializers import PostSerializer
        from apps.social.models import UserFollow

        target = get_object_or_404(
            User.objects.select_related("profile", "department"), username=username.lower(), is_active=True
        )
        profile = target.profile
        if not profile.can_be_viewed_by(request.user):
            raise Http404
        is_me = target.pk == request.user.pk
        posts = Post.objects.for_viewer(request.user).with_related().filter(author=target)
        if not is_me:
            posts = posts.filter(is_anonymous=False)
        posts = expand_reposts(list(annotate_for_user(posts, request.user)[:30]), request.user)
        avatar = profile.avatar.url if getattr(profile, "avatar", None) else None
        return Response({
            "id": target.pk,
            "username": target.username,
            "name": target.display_name,
            "badge": target.staff_badge,
            "avatar_url": absolute(request, avatar) if avatar else None,
            "bio": profile.bio,
            "department": target.department.name if target.department_id else "",
            "level": target.level,
            "nexscore": profile.nexscore,
            "is_me": is_me,
            "is_following": UserFollow.objects.filter(follower=request.user, following=target).exists(),
            "followers": target.follower_set.count(),
            "following": target.following_set.count(),
            "can_message": not is_me and _can_message(request.user, target),
            "posts": PostSerializer(posts, many=True, context={"request": request}).data,
        })


def _visible_post(request, pk):
    from apps.posts.api_views import visible_post

    return visible_post(request, pk)


class RepostAPIView(APIView):
    """POST {} reposts, {"action": "undo"} undoes, {"quote": "..."} quote-posts."""

    def post(self, request, pk):
        from apps.posts import services as posts
        from apps.posts.models import Post

        post = _visible_post(request, pk)
        if request.data.get("action") == "undo":
            run(posts.undo_repost, user=request.user, post=post)
            reposted = False
        else:
            run(posts.repost, user=request.user, post=post, quote=(request.data.get("quote") or "").strip())
            reposted = not (request.data.get("quote") or "").strip()
        original = post.repost_of if post.kind == Post.Kind.REPOST and post.repost_of_id else post
        original.refresh_from_db(fields=["repost_count"])
        return Response({"reposted": reposted, "count": original.repost_count})


class EditPostAPIView(APIView):
    def post(self, request, pk):
        from apps.posts import services as posts
        from apps.posts.serializers import PostSerializer

        post = _visible_post(request, pk)
        if post.author_id != request.user.pk:
            raise exceptions.PermissionDenied("You can only edit your own posts.")
        title = request.data.get("title")
        run(posts.edit_post, user=request.user, post=post, body=request.data.get("body", ""),
            title=None if title is None else title)
        return Response(PostSerializer(_visible_post(request, pk), context={"request": request}).data)

from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import linebreaksbr, urlize
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.posts.services import RateLimited

from . import services
from .models import GROUP_MAX_MEMBERS, Conversation, ConversationMember, Message, MessageAttachment


def _err(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


def _conversation(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk)
    try:
        services.membership(request.user, conversation)
    except PermissionDenied:
        raise Http404
    return conversation


def message_json(message, user):
    from django.urls import reverse

    gone = message.is_deleted or message.is_hidden
    return {
        "id": message.pk, "mine": message.sender_id == user.pk, "deleted": gone, "system": message.is_system,
        "sender": message.sender.full_name,
        "attachments": [] if gone else [{
            "kind": a.kind, "name": a.original_name, "ext": a.extension, "size": a.size,
            "url": reverse("messaging:attachment", args=[message.conversation_id, a.pk]),
        } for a in message.attachments.all()],
        "html": "" if gone else str(linebreaksbr(urlize(escape(message.body)))),
        "time": timezone.localtime(message.created_at).strftime("%H:%M"),
        "date": timezone.localtime(message.created_at).strftime("%a %d %b"),
    }


@login_required
def inbox_view(request):
    return render(request, "messaging/inbox.html", {"rows": services.inbox(request.user)})


@login_required
@require_POST
def start_view(request, username):
    other = get_object_or_404(User, username=username, is_active=True)
    try:
        conversation = services.start_conversation(request.user, other)
    except (PermissionDenied, ValidationError) as exc:
        flash.error(request, _err(exc))
        return redirect("accounts:profile", username=username)
    except RateLimited as exc:
        flash.error(request, str(exc))
        return redirect("accounts:profile", username=username)
    return redirect(conversation.get_absolute_url())


@login_required
def conversation_view(request, pk):
    conversation = _conversation(request, pk)
    other = services.other_member(conversation, request.user)
    items = list(conversation.messages.select_related("sender").prefetch_related("attachments")
                 .order_by("-id")[:100])[::-1]
    services.mark_read(request.user, conversation)
    member = services.membership(request.user, conversation)
    if conversation.is_group:
        allowed, reason = True, ""
    else:
        allowed, reason = services.can_message(request.user, other) if other else (False, "")
    return render(request, "messaging/conversation.html", {
        "is_admin": member.is_admin, "member_count": conversation.members.count(),
        "conversation": conversation, "other": other, "items": items, "can_send": allowed, "reason": reason,
        "muted": member.is_muted, "blocked": other is not None and services.has_blocked(request.user, other),
        "last_id": items[-1].pk if items else 0,
    })


@login_required
@require_POST
def send_view(request, pk):
    conversation = _conversation(request, pk)
    wants_json = request.headers.get("Accept") == "application/json"
    try:
        message = services.send_message(sender=request.user, conversation=conversation, body=request.POST.get("body", ""),
                                        files=request.FILES.getlist("files"))
    except (PermissionDenied, ValidationError) as exc:
        if wants_json:
            return JsonResponse({"error": {"message": _err(exc)}}, status=400)
        flash.error(request, _err(exc))
        return redirect(conversation.get_absolute_url())
    except RateLimited as exc:
        if wants_json:
            return JsonResponse({"error": {"message": str(exc)}}, status=429)
        flash.error(request, str(exc))
        return redirect(conversation.get_absolute_url())
    if wants_json:
        return JsonResponse({"message": message_json(message, request.user)})
    return redirect(conversation.get_absolute_url() + "#latest")


@login_required
def poll_view(request, pk):
    """New messages since `after` (and which earlier ones were deleted), for the live chat."""
    conversation = _conversation(request, pk)
    after = int(request.GET.get("after", "0") or 0)
    new = services.messages_after(conversation, after)
    if new:
        services.mark_read(request.user, conversation)
    since = request.GET.get("since_id")
    deleted = []
    if since and since.isdigit():
        deleted = list(conversation.messages.filter(id__gte=int(since), id__lte=after)
                       .filter(is_deleted=True).values_list("id", flat=True))
    return JsonResponse({"messages": [message_json(m, request.user) for m in new], "deleted": deleted})


@login_required
@require_POST
def delete_message_view(request, pk, message_id):
    conversation = _conversation(request, pk)
    message = get_object_or_404(Message, pk=message_id, conversation=conversation)
    try:
        services.delete_message(user=request.user, message=message)
    except PermissionDenied as exc:
        flash.error(request, str(exc))
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"deleted": message.pk})
    return redirect(conversation.get_absolute_url())


@login_required
@require_POST
def conversation_action_view(request, pk):
    conversation = _conversation(request, pk)
    other = services.other_member(conversation, request.user)
    action = request.POST.get("action")
    if action == "mute":
        muted = services.toggle_mute(request.user, conversation)
        flash.success(request, "Conversation muted." if muted else "Conversation unmuted.")
    elif action == "hide":
        services.hide_conversation(request.user, conversation)
        flash.success(request, "Conversation removed from your inbox. It comes back if a new message arrives.")
        return redirect("messaging:inbox")
    elif action == "leave" and conversation.is_group:
        services.leave_group(user=request.user, group=conversation)
        flash.success(request, f'You left "{conversation.title}".')
        return redirect("messaging:inbox")
    elif action in ("block", "unblock") and other is not None:
        if action == "block":
            services.block(request.user, other)
            flash.success(request, f"You blocked {other.full_name}. Neither of you can message the other.")
        else:
            services.unblock(request.user, other)
            flash.success(request, f"You unblocked {other.full_name}.")
    return redirect(conversation.get_absolute_url())


@login_required
@require_POST
def block_user_view(request, username):
    other = get_object_or_404(User, username=username)
    if services.has_blocked(request.user, other):
        services.unblock(request.user, other)
        flash.success(request, f"You unblocked {other.full_name}.")
    else:
        try:
            services.block(request.user, other)
            flash.success(request, f"You blocked {other.full_name}. Neither of you can message the other.")
        except ValidationError as exc:
            flash.error(request, _err(exc))
    return redirect(request.POST.get("next") or "messaging:inbox")


@login_required
def live_view(request):
    """Lightweight counts for badges, polled every 15 seconds while a tab is visible."""
    from apps.notifications.services import unread_count

    return JsonResponse({"notifications": unread_count(request.user),
                         "messages": services.unread_count(request.user)})



@login_required
def attachment_view(request, pk, attachment_id):
    """Only people in the conversation can open its photos and files."""
    from django.http import FileResponse, HttpResponseRedirect

    from apps.core.storage import download_url

    conversation = _conversation(request, pk)
    attachment = get_object_or_404(MessageAttachment.objects.select_related("message"), pk=attachment_id,
                                   message__conversation=conversation)
    if attachment.message.is_deleted or attachment.message.is_hidden:
        raise Http404
    as_attachment = attachment.kind == MessageAttachment.Kind.FILE
    from django.core.files.storage import FileSystemStorage

    if isinstance(attachment.file.storage, FileSystemStorage):
        return FileResponse(attachment.file.open("rb"), as_attachment=as_attachment,
                            filename=attachment.original_name, content_type=attachment.content_type)
    return HttpResponseRedirect(download_url(attachment.file, attachment.original_name, as_attachment=as_attachment))


def _pickable_people(user, exclude_ids=()):
    """People the user may add to a group: their department, minus anyone they can't message."""
    people = (User.objects.filter(department_id=user.department_id, is_active=True, email_verified=True)
              .exclude(pk=user.pk).exclude(pk__in=exclude_ids).select_related("profile").order_by("full_name")[:300])
    return [p for p in people if services.can_message(user, p)[0]]


@login_required
def new_group_view(request):
    error = None
    if request.method == "POST":
        chosen = User.objects.filter(pk__in=request.POST.getlist("members"), department_id=request.user.department_id)
        try:
            group = services.create_group(creator=request.user, title=request.POST.get("title", ""), members=list(chosen))
        except (PermissionDenied, ValidationError) as exc:
            error = _err(exc)
        except RateLimited as exc:
            error = str(exc)
        else:
            return redirect(group.get_absolute_url())
    return render(request, "messaging/new_group.html", {
        "people": _pickable_people(request.user), "error": error, "max": GROUP_MAX_MEMBERS,
        "chosen": set(map(str, request.POST.getlist("members"))), "title": request.POST.get("title", ""),
    })


@login_required
def group_view(request, pk):
    conversation = _conversation(request, pk)
    if not conversation.is_group:
        raise Http404
    me = services.membership(request.user, conversation)
    members = list(conversation.members.select_related("user__profile").order_by("-is_admin", "user__full_name"))
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "rename":
                services.rename_group(actor=request.user, group=conversation, title=request.POST.get("title", ""))
                flash.success(request, "Group renamed.")
            elif action == "add":
                people = list(User.objects.filter(pk__in=request.POST.getlist("members"),
                                                  department_id=request.user.department_id))
                added = services.add_members(actor=request.user, group=conversation, people=people)
                flash.success(request, f"Added {len(added)} {'person' if len(added) == 1 else 'people'}.")
            elif action in ("remove", "admin"):
                person = get_object_or_404(User, pk=request.POST.get("user"))
                if action == "remove":
                    services.remove_member(actor=request.user, group=conversation, person=person)
                    flash.success(request, f"{person.full_name} was removed.")
                else:
                    services.make_admin(actor=request.user, group=conversation, person=person)
                    flash.success(request, f"{person.full_name} is now a group admin.")
        except (PermissionDenied, ValidationError) as exc:
            flash.error(request, _err(exc))
        return redirect("messaging:group", pk=conversation.pk)
    return render(request, "messaging/group.html", {
        "group": conversation, "members": members, "is_admin": me.is_admin,
        "addable": _pickable_people(request.user, exclude_ids=[m.user_id for m in members]) if me.is_admin else [],
        "max": GROUP_MAX_MEMBERS,
    })

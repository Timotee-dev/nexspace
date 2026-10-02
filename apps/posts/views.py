from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.topics.models import Topic

from . import feed, services
from .forms import CommentForm, ComposerForm
from .models import Attachment, Bookmark, Comment, CommentVote, Post


def _back(request, fallback="/"):
    target = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return HttpResponseRedirect(target)
    return redirect(fallback)


def _error_message(exc):
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


def get_visible_post(user, pk):
    post = get_object_or_404(feed.annotate_for_user(Post.objects.with_related(), user), pk=pk)
    if not services.can_view(user, post):
        raise Http404
    return post


# --- Feed page (rendered by core.home_view) -----------------------------------
def feed_context(request):
    tab = request.GET.get("tab", "for-you")
    if tab not in feed.TABS:
        tab = "for-you"
    try:
        page = max(int(request.GET.get("page", 1)), 1)
        before = int(request.GET["before"]) if request.GET.get("before") else None
    except ValueError:
        page, before = 1, None
    posts, next_params = feed.get_feed(request.user, tab, page=page, before=before)
    next_url = None
    if next_params:
        query = "&".join(f"{k}={v}" for k, v in next_params.items())
        next_url = f"{reverse('core:home')}?tab={tab}&{query}"
    from django.db.models import Max

    max_id = Post.objects.for_viewer(request.user).aggregate(m=Max("id"))["m"] or 0
    return {"tab": tab, "posts": posts, "next_url": next_url, "is_first_page": page == 1 and before is None,
            "max_post_id": max_id}


# --- Compose ----------------------------------------------------------------
@login_required
def compose_view(request):
    can_official = services.can_post_official(request.user)
    if request.method == "POST":
        form = ComposerForm(
            request.POST, poll_options=request.POST.getlist("poll_option"), can_post_official=can_official,
            user=request.user,
        )
        if not request.user.can_write:
            messages.error(request, "Your account can't post right now.")
        elif form.is_valid():
            try:
                post = services.create_post(
                    author=request.user,
                    images=request.FILES.getlist("images"),
                    files=request.FILES.getlist("files"),
                    **form.service_kwargs(),
                )
            except (ValidationError, PermissionDenied, services.RateLimited) as exc:
                form.add_error(None, _error_message(exc))
            else:
                messages.success(request, "Posted.")
                return redirect(post.get_absolute_url())
    else:
        initial_kind = request.GET.get("type") if request.GET.get("type") in Post.Kind.values and \
            request.GET.get("type") != Post.Kind.REPOST else Post.Kind.POST
        initial = {"kind": initial_kind}
        if request.GET.get("space"):
            initial["space"] = request.user.space_memberships.filter(space__slug=request.GET["space"]).values_list(
                "space_id", flat=True).first()
        form = ComposerForm(initial=initial, can_post_official=can_official, user=request.user)
    poll_options = request.POST.getlist("poll_option") if request.method == "POST" else []
    poll_options = (poll_options + ["", ""])[: max(2, len(poll_options))]
    return render(request, "posts/compose.html", {"form": form, "poll_options": poll_options})


# --- Detail & comments ----------------------------------------------------------
def build_thread(post, user):
    comments = list(
        Comment.objects.filter(post=post)
        .select_related("author__profile", "author__staff_profile", "reply_to")
        .annotate(
            my_vote_up=Exists(CommentVote.objects.filter(comment=OuterRef("pk"), user=user, value=1)),
            my_vote_down=Exists(CommentVote.objects.filter(comment=OuterRef("pk"), user=user, value=-1)),
        )
    )
    children = {}
    for c in comments:
        c.children = []
        children.setdefault(c.parent_id, []).append(c)
    for c in comments:
        c.children = children.get(c.pk, [])

    def prune(nodes):
        kept = []
        for node in nodes:
            node.children = prune(node.children)
            if not node.is_deleted or node.children:
                kept.append(node)
        return kept

    top = prune(children.get(None, []))
    if post.accepted_comment_id:  # accepted answer floats to the top
        top.sort(key=lambda c: c.pk != post.accepted_comment_id)
    return top


@login_required
def detail_view(request, pk):
    post = get_visible_post(request.user, pk)
    if post.kind == Post.Kind.REPOST and post.repost_of_id:
        return redirect(post.repost_of.get_absolute_url())
    post.is_reposted = post.pk in services.reposted_ids(request.user, [post])
    return render(request, "posts/detail.html", {
        "post": post,
        "thread": build_thread(post, request.user),
        "comment_form": CommentForm(),
        "is_asker": post.kind == Post.Kind.QUESTION and post.author_id == request.user.pk,
        "can_moderate": request.user.can_moderate(post.department),
        "max_comment_id": post.comments.order_by("-id").values_list("id", flat=True).first() or 0,
    })


@login_required
@require_POST
def comment_create_view(request, pk):
    post = get_visible_post(request.user, pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Write a comment first (up to 1,000 characters).")
        return redirect(post.get_absolute_url())
    parent = None
    if form.cleaned_data.get("parent"):
        parent = Comment.objects.filter(pk=form.cleaned_data["parent"], post=post).first()
    try:
        comment = services.add_comment(user=request.user, post=post, body=form.cleaned_data["body"], parent=parent)
    except (ValidationError, PermissionDenied, services.RateLimited) as exc:
        messages.error(request, _error_message(exc))
        return redirect(post.get_absolute_url())
    return redirect(f"{post.get_absolute_url()}#c-{comment.pk}")


@login_required
@require_POST
def comment_delete_view(request, pk):
    comment = get_object_or_404(Comment, pk=pk)
    try:
        services.delete_comment(user=request.user, comment=comment)
        messages.success(request, "Comment deleted.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return redirect(comment.post.get_absolute_url())


@login_required
@require_POST
def post_delete_view(request, pk):
    post = get_visible_post(request.user, pk)
    try:
        services.delete_post(user=request.user, post=post)
        messages.success(request, "Post deleted.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return redirect(post.get_absolute_url())
    return redirect("core:home")


# --- No-JavaScript fallbacks for quick actions (JS uses the API instead) ------
@login_required
@require_POST
def post_vote_view(request, pk):
    post = get_visible_post(request.user, pk)
    try:
        services.vote_post(user=request.user, post=post, value=int(request.POST.get("value", 0)))
    except (ValidationError, PermissionDenied, services.RateLimited, ValueError) as exc:
        messages.error(request, _error_message(exc))
    return _back(request, post.get_absolute_url())


@login_required
@require_POST
def comment_vote_view(request, pk):
    comment = get_object_or_404(Comment.objects.select_related("post"), pk=pk)
    try:
        services.vote_comment(user=request.user, comment=comment, value=int(request.POST.get("value", 0)))
    except (ValidationError, PermissionDenied, services.RateLimited, ValueError) as exc:
        messages.error(request, _error_message(exc))
    return _back(request, comment.post.get_absolute_url())


@login_required
@require_POST
def accept_answer_view(request, pk):
    comment = get_object_or_404(Comment.objects.select_related("post"), pk=pk)
    try:
        services.accept_answer(user=request.user, comment=comment)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _error_message(exc))
    return redirect(f"{comment.post.get_absolute_url()}#c-{comment.pk}")


@login_required
@require_POST
def bookmark_view(request, pk):
    post = get_visible_post(request.user, pk)
    saved = services.toggle_bookmark(user=request.user, post=post)
    messages.success(request, "Saved." if saved else "Removed from saved.")
    return _back(request, post.get_absolute_url())


@login_required
@require_POST
def poll_vote_view(request, pk):
    post = get_visible_post(request.user, pk)
    try:
        services.vote_poll(user=request.user, post=post, option_ids=request.POST.getlist("option"))
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, _error_message(exc))
    return _back(request, post.get_absolute_url())


@login_required
def attachment_download_view(request, pk, attachment_id):
    post = get_visible_post(request.user, pk)
    attachment = get_object_or_404(Attachment, pk=attachment_id, post=post)
    storage = attachment.file.storage
    if hasattr(storage, "path"):
        try:
            return FileResponse(
                attachment.file.open("rb"), as_attachment=attachment.kind == Attachment.Kind.FILE,
                filename=attachment.original_name, content_type=attachment.content_type,
            )
        except NotImplementedError:
            pass
    return HttpResponseRedirect(attachment.file.url)


# --- Saved & topics -----------------------------------------------------------
@login_required
def saved_view(request):
    order = list(
        Bookmark.objects.filter(user=request.user, post__is_deleted=False).values_list("post_id", flat=True)[:200]
    )
    by_id = {
        p.pk: p
        for p in feed.annotate_for_user(Post.objects.for_viewer(request.user).with_related(), request.user)
        .filter(pk__in=order)
    }
    posts = [by_id[pk] for pk in order if pk in by_id]
    return render(request, "posts/saved.html", {"posts": posts})


@login_required
def topic_view(request, slug):
    from apps.social.models import TopicFollow

    topic = get_object_or_404(Topic, slug=slug, is_active=True)
    posts = list(
        feed.annotate_for_user(Post.objects.for_viewer(request.user).with_related(), request.user)
        .filter(topics=topic).exclude(kind=Post.Kind.REPOST)[:50]
    )
    return render(request, "posts/topic.html", {
        "topic": topic,
        "posts": posts,
        "is_following": TopicFollow.objects.filter(user=request.user, topic=topic).exists(),
        "follower_count": topic.followers.count(),
    })




# --- Editing -------------------------------------------------------------------------
def _error_text(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


@login_required
def edit_post_view(request, pk):
    post = get_visible_post(request.user, pk)
    if post.author_id != request.user.pk:
        raise PermissionDenied
    topics = Topic.objects.filter(is_active=True)
    error = None
    if request.method == "POST":
        try:
            chosen = topics.filter(pk__in=request.POST.getlist("topics"))
            services.edit_post(user=request.user, post=post, body=request.POST.get("body", ""),
                               title=request.POST.get("title") if "title" in request.POST else None, topics=chosen)
        except (ValidationError, PermissionDenied) as exc:
            error = _error_text(exc)
        except services.RateLimited as exc:
            error = str(exc)
        else:
            messages.success(request, "Post updated.")
            return redirect(post.get_absolute_url())
    poll_locked = post.kind == Post.Kind.POLL and post.poll.votes.exists()
    return render(request, "posts/edit.html", {
        "post": post, "topics": topics, "chosen": set(post.topics.values_list("pk", flat=True)),
        "error": error, "poll_locked": poll_locked,
        "body": request.POST.get("body", post.body) if request.method == "POST" else post.body,
    })


@login_required
def edit_comment_view(request, pk):
    comment = get_object_or_404(Comment.objects.select_related("post"), pk=pk)
    if not services.can_view(request.user, comment.post) or comment.author_id != request.user.pk:
        raise Http404
    error = None
    if request.method == "POST":
        try:
            services.edit_comment(user=request.user, comment=comment, body=request.POST.get("body", ""))
        except (ValidationError, PermissionDenied) as exc:
            error = _error_text(exc)
        except services.RateLimited as exc:
            error = str(exc)
        else:
            messages.success(request, "Comment updated.")
            return redirect(f"{comment.post.get_absolute_url()}#c-{comment.pk}")
    return render(request, "posts/edit_comment.html", {"comment": comment, "error": error,
                                                       "body": request.POST.get("body", comment.body)})


@login_required
def history_view(request, pk):
    """Earlier versions of an edited post and its comments: for the author and moderators."""
    post = get_visible_post(request.user, pk)
    if post.author_id != request.user.pk and not request.user.can_moderate(post.department):
        raise PermissionDenied
    comments = (Comment.objects.filter(post=post, edited_at__isnull=False).select_related("author")
                .prefetch_related("revisions"))
    if post.author_id != request.user.pk and not request.user.can_moderate(post.department):
        comments = comments.filter(author=request.user)
    return render(request, "posts/history.html", {"post": post, "revisions": post.revisions.all(),
                                                  "comments": comments})


# --- Reposts -------------------------------------------------------------------------
@login_required
@require_POST
def repost_view(request, pk):
    post = get_visible_post(request.user, pk)
    try:
        if request.POST.get("action") == "undo":
            services.undo_repost(user=request.user, post=post)
            reposted = False
        else:
            services.repost(user=request.user, post=post)
            reposted = True
    except (ValidationError, PermissionDenied) as exc:
        if request.headers.get("Accept") == "application/json":
            from django.http import JsonResponse

            return JsonResponse({"error": {"message": _error_text(exc)}}, status=400)
        messages.error(request, _error_text(exc))
        return _back(request, post.get_absolute_url())
    except services.RateLimited as exc:
        messages.error(request, str(exc))
        return _back(request, post.get_absolute_url())
    original = post.repost_of if post.kind == Post.Kind.REPOST and post.repost_of_id else post
    original.refresh_from_db(fields=["repost_count"])
    if request.headers.get("Accept") == "application/json":
        from django.http import JsonResponse

        return JsonResponse({"reposted": reposted, "count": original.repost_count})
    messages.success(request, "Reposted." if reposted else "Repost removed.")
    return _back(request, post.get_absolute_url())


@login_required
def quote_view(request, pk):
    post = get_visible_post(request.user, pk)
    if post.kind == Post.Kind.REPOST and post.repost_of_id:
        post = get_visible_post(request.user, post.repost_of_id)
    if not services.can_repost(request.user, post):
        raise PermissionDenied
    error = None
    if request.method == "POST":
        try:
            new = services.repost(user=request.user, post=post, quote=request.POST.get("body", ""))
        except (ValidationError, PermissionDenied) as exc:
            error = _error_text(exc)
        except services.RateLimited as exc:
            error = str(exc)
        else:
            if not new.body:
                error = "Write something, or use Repost instead."
            else:
                messages.success(request, "Quoted.")
                return redirect(new.get_absolute_url())
    return render(request, "posts/quote.html", {"original": post, "error": error,
                                                "body": request.POST.get("body", "")})



# --- Live updates (polled while the page is visible) -------------------------------------
@login_required
def live_feed_view(request):
    from django.http import JsonResponse

    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    return JsonResponse({"new": feed.new_count(request.user, request.GET.get("tab", "for-you"), after)})


@login_required
def live_comments_view(request, pk):
    from django.http import JsonResponse

    post = get_visible_post(request.user, pk)
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    new = (Comment.objects.filter(post=post, id__gt=after, is_deleted=False, is_hidden=False)
           .exclude(author=request.user).count())
    return JsonResponse({"new": new, "comment_count": post.comment_count})

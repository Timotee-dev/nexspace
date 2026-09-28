from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core import ratelimit

from . import services
from .forms import AppearanceForm, InterestsForm, LevelForm, LoginForm, PrivacyForm, ProfileForm, SignupForm
from .models import User

RESEND_COOLDOWN_SECONDS = 60


def _safe_next(request, fallback):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return fallback


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        user = services.register_user(
            email=data["email"],
            password=data["password"],
            full_name=data["full_name"],
            department=data["department"],
            level=data["level"],
            matric_number=data["matric_number"],
        )
        services.send_verification_email(user)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"Account created. We sent a verification link to {user.email}.")
        return redirect("accounts:onboarding")
    return render(request, "accounts/signup.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = LoginForm(request, data=request.POST or None)
    locked = False
    if request.method == "POST":
        email = request.POST.get("email", "")
        if ratelimit.is_locked(request, email):
            locked = True
            form = LoginForm(request)  # don't even try the password
        elif form.is_valid():
            ratelimit.clear_failures(request, email)
            login(request, form.user)
            return redirect(_safe_next(request, reverse("core:home")))
        else:
            ratelimit.record_failure(request, email)
            locked = ratelimit.is_locked(request, email)
    status = 429 if locked else 200
    return render(request, "accounts/login.html", {"form": form, "locked": locked}, status=status)


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You're logged out.")
    return redirect("accounts:login")


def verify_email_view(request, token):
    user = services.read_verification_token(token)
    if user is None:
        return render(request, "accounts/verify_failed.html", status=400)
    services.mark_verified(user)
    messages.success(request, "Email verified. You now have full access to NexSpace.")
    return redirect("core:home" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_verification_view(request):
    user = request.user
    if user.email_verified:
        messages.info(request, "Your email is already verified.")
    elif not cache.add(f"resend-verify:{user.pk}", 1, RESEND_COOLDOWN_SECONDS):
        messages.error(request, "A verification email was sent less than a minute ago. Check your inbox.")
    else:
        services.send_verification_email(user)
        messages.success(request, f"Verification email sent to {user.email}.")
    return redirect(_safe_next(request, reverse("core:home")))


# --- Onboarding --------------------------------------------------------------
ONBOARDING_STEPS = ["interests", "level", "spaces"]


@login_required
def onboarding_view(request):
    user = request.user
    step = request.GET.get("step", "interests")
    if step not in ONBOARDING_STEPS:
        step = "interests"
    step_number = ONBOARDING_STEPS.index(step) + 1

    if step == "interests":
        form = InterestsForm(
            request.POST or None, initial={"interests": user.profile.interests.all()}
        )
        if request.method == "POST" and form.is_valid():
            user.profile.interests.set(form.cleaned_data["interests"])
            return redirect(f"{reverse('accounts:onboarding')}?step=level")
    elif step == "level":
        form = LevelForm(request.POST or None, initial={"level": user.level})
        if request.method == "POST" and form.is_valid():
            user.level = form.cleaned_data["level"]
            user.save(update_fields=["level"])
            return redirect(f"{reverse('accounts:onboarding')}?step=spaces")
    else:
        from apps.spaces import services as space_services
        from apps.spaces.models import Space

        suggested = list(
            Space.objects.filter(department_id=user.department_id)
            .filter(Q(kind__in=["department", "community"]) | Q(level=user.level))
            .select_related("course").order_by("kind", "name")[:40]
        )
        joined = space_services.joined_space_ids(user)
        pending = space_services.pending_request_ids(user)
        preselect = {s.pk for s in suggested if s.pk in joined or s.pk in pending
                     or s.kind in ("department", "level") or (s.kind == "course" and s.level == user.level)}
        if request.method == "POST":
            chosen = {int(i) for i in request.POST.getlist("spaces") if i.isdigit()}
            requested = 0
            for space in suggested:
                if space.pk in chosen and space.pk not in joined:
                    try:
                        if space_services.request_to_join(user, space) == "requested":
                            requested += 1
                    except PermissionDenied:
                        pass  # e.g. email not verified yet; they can ask again later
                elif space.pk not in chosen and space.pk in joined:
                    space_services.leave(user, space)
            if requested:
                messages.info(request, f"Sent {requested} request{'s' if requested > 1 else ''} to join. "
                                       "You'll be notified when you're approved.")
            user.onboarding_completed = True
            user.save(update_fields=["onboarding_completed"])
            messages.success(request, "You're all set. Welcome to NexSpace.")
            return redirect("core:home")
        form = None
        for space in suggested:
            space.preselected = space.pk in preselect
        return render(request, "accounts/onboarding.html", {
            "step": step, "step_number": step_number, "step_total": len(ONBOARDING_STEPS), "suggested": suggested,
        })

    return render(
        request,
        "accounts/onboarding.html",
        {"form": form, "step": step, "step_number": step_number, "step_total": len(ONBOARDING_STEPS)},
    )


# --- Profiles & settings -----------------------------------------------------
PROFILE_TABS = ("posts", "replies", "about")


@login_required
def profile_detail_view(request, username):
    from apps.posts.feed import annotate_for_user
    from apps.posts.models import Comment, Post
    from apps.social.models import UserFollow

    profile_user = get_object_or_404(
        User.objects.select_related("profile", "department__faculty__university"),
        username=username.lower(),
        is_active=True,
    )
    profile = profile_user.profile
    if not profile.can_be_viewed_by(request.user):
        raise Http404
    is_owner = profile_user.pk == request.user.pk
    tab = request.GET.get("tab", "posts")
    if tab not in PROFILE_TABS:
        tab = "posts"

    context = {
        "profile_user": profile_user,
        "profile": profile,
        "is_owner": is_owner,
        "tab": tab,
        "show_matric": profile_user.matric_number and (is_owner or profile.show_matric_number),
        "show_links": is_owner or profile.show_social_links,
        "interests": profile.interests.all(),
        "completion": profile.completion_steps() if is_owner else None,
        "follower_count": profile_user.follower_set.count(),
        "following_count": profile_user.following_set.count(),
        "is_following": UserFollow.objects.filter(follower=request.user, following=profile_user).exists(),
    }
    if tab == "posts":
        posts = Post.objects.for_viewer(request.user).with_related().filter(author=profile_user)
        if not is_owner:
            posts = posts.filter(is_anonymous=False)  # anonymous posts never appear on a public profile
        context["posts"] = list(annotate_for_user(posts, request.user)[:30])
    elif tab == "replies":
        context["replies"] = list(
            Comment.objects.filter(
                author=profile_user, is_deleted=False, post__is_deleted=False,
                post__department_id=request.user.department_id,
            ).select_related("post").order_by("-created_at")[:30]
        )
    return render(request, "profiles/detail.html", context)


@login_required
def settings_profile_view(request):
    user = request.user
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile saved.")
            return redirect("accounts:settings-profile")
    else:
        form = ProfileForm(initial=ProfileForm.initial_for(user), user=user)
    return render(request, "accounts/settings_profile.html", {"form": form, "section": "profile"})


@login_required
def settings_privacy_view(request):
    form = PrivacyForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Privacy settings saved.")
        return redirect("accounts:settings-privacy")
    return render(request, "accounts/settings_privacy.html", {"form": form, "section": "privacy"})


@login_required
def settings_account_view(request):
    appearance = AppearanceForm(instance=request.user.profile, prefix="appearance")
    password_form = PasswordChangeForm(request.user, prefix="password")
    if request.method == "POST":
        if "save_appearance" in request.POST:
            appearance = AppearanceForm(request.POST, instance=request.user.profile, prefix="appearance")
            if appearance.is_valid():
                appearance.save()
                messages.success(request, "Appearance saved.")
                return redirect("accounts:settings-account")
        elif "change_password" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST, prefix="password")
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed.")
                return redirect("accounts:settings-account")
    return render(
        request,
        "accounts/settings_account.html",
        {"appearance": appearance, "password_form": password_form, "section": "account"},
    )

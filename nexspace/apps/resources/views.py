from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import AcademicSession, Course, Level, Semester

from . import services
from .forms import RatingForm, ResourceUploadForm
from .models import Resource, ResourceRating


def _visible(request, pk):
    resource = get_object_or_404(Resource.objects.select_related("course__department", "session", "uploaded_by__profile"), pk=pk)
    if not services.can_view(request.user, resource):
        raise Http404
    return resource


@login_required
def library_view(request):
    user = request.user
    params = {
        "q": request.GET.get("q", "").strip()[:80],
        "course": request.GET.get("course") or None,
        "level": request.GET.get("level") if (request.GET.get("level") or "").isdigit() else None,
        "session": request.GET.get("session") if (request.GET.get("session") or "").isdigit() else None,
        "semester": request.GET.get("semester") if request.GET.get("semester") in ("1", "2") else None,
        "resource_type": request.GET.get("type") if request.GET.get("type") in Resource.Type.values else None,
        "sort": request.GET.get("sort") if request.GET.get("sort") in services.SORTS else "useful",
    }
    qs = Resource.objects.for_viewer(user).select_related("course", "session", "uploaded_by__staff_profile")
    resources = list(services.search(qs, **params)[:60])
    uni = user.department.faculty.university_id if user.department_id else None
    return render(request, "resources/library.html", {
        "resources": resources, "params": params, "sorts": services.SORTS,
        "types": Resource.Type.choices, "levels": Level.choices, "semesters": Semester.choices,
        "courses": Course.objects.filter(department_id=user.department_id, is_active=True),
        "sessions": AcademicSession.objects.filter(university_id=uni),
        "filtered": any(params[k] for k in ("q", "course", "level", "session", "semester", "resource_type")),
    })


@login_required
def upload_view(request):
    initial = {}
    if request.GET.get("course"):
        initial["course"] = Course.objects.filter(slug=request.GET["course"], department_id=request.user.department_id).first()
    if request.GET.get("type") in Resource.Type.values:
        initial["resource_type"] = request.GET["type"]
    form = ResourceUploadForm(request.POST or None, request.FILES or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            resource = services.upload(
                user=request.user, course=d["course"], title=d["title"], file=d["file"],
                resource_type=d["resource_type"], description=d["description"], exam_type=d["exam_type"],
                session=d["session"], semester=d["semester"],
            )
        except (ValidationError, PermissionDenied, services.RateLimited) as exc:
            form.add_error(None, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Published. Classmates can download it now.")
            return redirect(resource.get_absolute_url())
    return render(request, "resources/upload.html", {"form": form})


@login_required
def detail_view(request, pk):
    resource = _visible(request, pk)
    mine = ResourceRating.objects.filter(resource=resource, user=request.user).first()
    form = RatingForm(initial={"stars": mine.stars, "review": mine.review} if mine else None)
    return render(request, "resources/detail.html", {
        "resource": resource,
        "ratings": list(resource.ratings.exclude(review="").select_related("user__profile")[:30]),
        "my_rating": mine,
        "rating_form": form,
        "can_remove": resource.uploaded_by_id == request.user.pk or services.is_verified_uploader(request.user, resource.course),
        "nexai_ready": settings.NEXAI_ENABLED and resource.chunks.exists(),
    })


@login_required
def download_view(request, pk):
    resource = _visible(request, pk)
    services.record_download(user=request.user, resource=resource)
    if _is_local(resource.file):
        try:
            return FileResponse(resource.file.open("rb"), as_attachment=True, filename=resource.original_name,
                                content_type=resource.content_type)
        except NotImplementedError:
            pass
    from apps.core.storage import download_url

    return HttpResponseRedirect(download_url(resource.file, resource.original_name))


@login_required
@require_POST
def rate_view(request, pk):
    resource = _visible(request, pk)
    form = RatingForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick between 1 and 5 stars.")
    else:
        try:
            services.rate(user=request.user, resource=resource, stars=form.cleaned_data["stars"],
                          review=form.cleaned_data["review"])
            messages.success(request, "Thanks — your rating is saved.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect(f"{resource.get_absolute_url()}#ratings")


@login_required
@require_POST
def remove_view(request, pk):
    resource = _visible(request, pk)
    try:
        services.remove(user=request.user, resource=resource)
        messages.success(request, "Resource removed.")
        return redirect(resource.course.get_absolute_url() + "?tab=resources")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return redirect(resource.get_absolute_url())


def _is_local(fieldfile):
    """Local-disk files are streamed by Django; cloud files redirect straight to storage."""
    from django.core.files.storage import FileSystemStorage

    return isinstance(fieldfile.storage, FileSystemStorage)

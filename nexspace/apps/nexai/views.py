from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import Course
from apps.resources.models import Resource
from apps.resources.services import can_view

from . import llm, services

HISTORY_KEY = "nexai-history"


def _err(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


@login_required
def ask_view(request):
    user = request.user
    courses = Course.objects.filter(department_id=user.department_id, is_active=True)
    course = courses.filter(slug=request.POST.get("course") or request.GET.get("course")).first()
    history = request.session.get(HISTORY_KEY, [])
    error = None
    if request.method == "POST":
        if request.POST.get("clear"):
            request.session[HISTORY_KEY] = []
            return redirect(request.path + (f"?course={course.slug}" if course else ""))
        question = request.POST.get("question", "")
        try:
            result = services.ask(user=user, question=question, course=course,
                                  history=[h for h in history if h.get("a")])
        except (ValidationError, PermissionDenied, llm.NexAIError) as exc:
            error = _err(exc)
        else:
            history = (history + [{"q": " ".join(question.split())[:600], "a": result["answer"],
                                   "sources": result["sources"], "search_only": result["search_only"],
                                   "course": course.code if course else ""}])[-6:]
            request.session[HISTORY_KEY] = history
            return redirect(request.path + (f"?course={course.slug}" if course else "") + "#latest")
    return render(request, "nexai/ask.html", {
        "history": history, "courses": courses, "course": course, "error": error,
        "enabled": services.enabled(), "remaining": services.remaining_today(user),
        "suggestions": [
            f"What topics appear most in {course.code} past questions?" if course else "What is a binary search tree?",
            "When is my next exam?", "Explain Big-O notation with an example",
        ],
    })


def _resource(request, pk):
    resource = get_object_or_404(Resource.objects.select_related("course", "session"), pk=pk)
    if not can_view(request.user, resource):
        from django.http import Http404

        raise Http404
    return resource


@login_required
@require_POST
def summary_view(request, pk):
    resource = _resource(request, pk)
    try:
        result = services.summarize(user=request.user, resource=resource)
    except (ValidationError, PermissionDenied, llm.NexAIError) as exc:
        messages.error(request, _err(exc))
        return redirect(resource.get_absolute_url())
    return render(request, "nexai/result.html", {"title": f"Summary: {resource.title}", "resource": resource,
                                                 "result": result, "back": resource.get_absolute_url()})


@login_required
@require_POST
def quiz_view(request, pk):
    resource = _resource(request, pk)
    try:
        result = services.quiz(user=request.user, resource=resource)
    except (ValidationError, PermissionDenied, llm.NexAIError) as exc:
        messages.error(request, _err(exc))
        return redirect(resource.get_absolute_url())
    return render(request, "nexai/quiz.html", {"resource": resource, "result": result})


@login_required
@require_POST
def insights_view(request, slug):
    course = get_object_or_404(Course, slug=slug, department_id=request.user.department_id)
    try:
        result = services.insights(user=request.user, course=course)
    except (ValidationError, PermissionDenied, llm.NexAIError) as exc:
        messages.error(request, _err(exc))
        return redirect(course.get_absolute_url() + "?tab=past-questions")
    return render(request, "nexai/result.html", {"title": f"What comes up in {course.code} past questions",
                                                 "result": result, "back": course.get_absolute_url() + "?tab=past-questions"})

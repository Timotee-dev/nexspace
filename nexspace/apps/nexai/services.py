"""NexAI: answers grounded in the department's own course materials.

Every answer is built from passages NexAI retrieved from resources the student is
allowed to see, and cites them as [1], [2]. With no relevant passages it says so
instead of guessing. Without an API key it returns the passages themselves.
"""
import json
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from . import llm, retrieval
from .models import ResourceChunk, Usage

SYSTEM = """You are NexAI, the study assistant inside NexSpace, a university department's platform.
Rules:
- Answer ONLY from the numbered sources provided. Cite them inline like [1] or [2][3] after the sentences they support.
- If the sources don't contain the answer, say you couldn't find it in the course materials and suggest what the student could search or ask their course rep. Never invent facts, page numbers, dates or sources.
- Help students learn: explain step by step and show reasoning. If a question looks like a graded assignment, explain the method and concepts rather than handing over a finished answer.
- Be concise, friendly and clear. Use short paragraphs or simple "- " bullet lists. No headings, no tables.
- The "Your upcoming dates" block is the student's own calendar; use it for questions about deadlines, tests and exams."""

MAX_QUESTION = 600
MAX_CONTEXT_CHARS = 14000


def enabled():
    return settings.NEXAI_ENABLED


def remaining_today(user):
    since = timezone.now() - timedelta(days=1)
    return max(settings.NEXAI_DAILY_LIMIT - Usage.objects.filter(user=user, created_at__gte=since, used_model=True).count(), 0)


def _check(user):
    if not user.is_active or user.is_suspended:
        raise PermissionDenied("Your account can't use NexAI right now.")
    if enabled() and remaining_today(user) <= 0:
        raise PermissionDenied("You've reached today's NexAI limit. It resets over the next 24 hours.")


def _record(user, mode, used_model, tin=0, tout=0):
    Usage.objects.create(user=user, department_id=user.department_id, mode=mode, used_model=used_model,
                         input_tokens=tin, output_tokens=tout)


def _source_label(chunk):
    r = chunk.resource
    parts = [r.course.code, r.title]
    if r.session_id:
        parts.append(r.session.name)
    if chunk.page:
        parts.append(f"p. {chunk.page}")
    return " · ".join(parts)


def _sources(chunks):
    return [{"n": i + 1, "label": _source_label(c), "url": f"{c.resource.get_absolute_url()}",
             "resource_id": c.resource_id, "page": c.page, "excerpt": c.text[:280]} for i, c in enumerate(chunks)]


def _fit(chunks, limit=MAX_CONTEXT_CHARS):
    """Keep only as many passages as fit in the model's context budget."""
    out, total = [], 0
    for c in chunks:
        size = len(c.text) + 80
        if total + size > limit:
            break
        out.append(c)
        total += size
    return out


def _context(chunks):
    return "\n\n".join(f"[{i + 1}] {_source_label(c)}\n{c.text}" for i, c in enumerate(chunks))


def _calendar_block(user):
    from apps.notices.services import upcoming_for

    events = upcoming_for(user, limit=8)
    if not events:
        return "Your upcoming dates: none on the calendar."
    lines = [f"- {timezone.localtime(e.starts_at):%a %d %b %Y, %H:%M}: "
             f"{(e.target_course.code + ' ') if e.target_course_id else ''}{e.get_kind_display()} — {e.title}"
             for e in events]
    return "Your upcoming dates:\n" + "\n".join(lines)


def ask(*, user, question, course=None, history=()):
    question = " ".join((question or "").split())[:MAX_QUESTION]
    if len(question) < 3:
        raise ValidationError("Ask a question first.")
    _check(user)
    chunks = _fit(retrieval.search(user, question, course=course, k=6))
    sources = _sources(chunks)
    if not enabled():
        _record(user, Usage.Mode.ASK, False)
        return {"answer": "", "sources": sources, "search_only": True}
    wants_dates = re.search(r"\b(when|deadline|exam|test|due|date|schedule|timetable)\b", question, re.I)
    if not chunks and not wants_dates:
        _record(user, Usage.Mode.ASK, False)
        return {"answer": "I couldn't find anything about that in your department's uploaded materials yet. "
                          "Try different keywords, check the course's Resources tab, or ask your course rep to upload notes on it.",
                "sources": [], "search_only": False}
    messages = []
    for turn in list(history)[-4:]:
        messages += [{"role": "user", "content": turn["q"]}, {"role": "assistant", "content": turn["a"]}]
    context = _context(chunks) or "(no matching course materials)"
    messages.append({"role": "user", "content": f"Sources:\n{context}\n\n{_calendar_block(user)}\n\nQuestion: {question}"})
    answer, tin, tout = llm.complete(system=SYSTEM, messages=messages)
    _record(user, Usage.Mode.ASK, True, tin, tout)
    return {"answer": answer, "sources": sources, "search_only": False}


def _resource_chunks(user, resource, limit_chars=MAX_CONTEXT_CHARS):
    from apps.resources.services import can_view

    if not can_view(user, resource):
        raise PermissionDenied("You can't use NexAI on this resource.")
    chunks = list(ResourceChunk.objects.filter(resource=resource).select_related("resource__course", "resource__session"))
    if not chunks:
        raise ValidationError(resource.index_error or "NexAI hasn't read this file yet. Try again in a few minutes.")
    return _fit(chunks, limit_chars)


def summarize(*, user, resource):
    _check(user)
    chunks = _resource_chunks(user, resource)
    if not enabled():
        raise ValidationError("Summaries need NexAI's writing model, which isn't set up on this server.")
    prompt = (f"Sources:\n{_context(chunks)}\n\nSummarise this {resource.get_resource_type_display().lower()} for a student "
              "revising for exams: the main topics, key definitions and formulas, and what to focus on. Cite sources.")
    answer, tin, tout = llm.complete(system=SYSTEM, messages=[{"role": "user", "content": prompt}], max_tokens=1000)
    _record(user, Usage.Mode.SUMMARY, True, tin, tout)
    return {"answer": answer, "sources": _sources(chunks)}


def quiz(*, user, resource, count=5):
    _check(user)
    chunks = _resource_chunks(user, resource)
    if not enabled():
        raise ValidationError("Quizzes need NexAI's writing model, which isn't set up on this server.")
    prompt = (f"Sources:\n{_context(chunks)}\n\nWrite {count} multiple-choice questions that test understanding of these "
              "sources. Reply with JSON only, no other text, in this shape: "
              '{"questions":[{"question":"...","options":["...","...","...","..."],"answer":0,"explanation":"... [1]"}]} '
              "where answer is the index of the correct option.")
    raw, tin, tout = llm.complete(system=SYSTEM, messages=[{"role": "user", "content": prompt}], max_tokens=1600,
                                  temperature=0.4)
    _record(user, Usage.Mode.QUIZ, True, tin, tout)
    try:
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M))
        questions = [q for q in data["questions"] if len(q.get("options", [])) >= 2
                     and isinstance(q.get("answer"), int) and 0 <= q["answer"] < len(q["options"])]
    except (ValueError, KeyError, TypeError):
        raise ValidationError("NexAI couldn't build a quiz from this file. Try again.")
    if not questions:
        raise ValidationError("NexAI couldn't build a quiz from this file. Try again.")
    return {"questions": questions[:count], "sources": _sources(chunks)}


def insights(*, user, course):
    """What topics come up most in this course's past questions?"""
    _check(user)
    chunks = _fit(list(retrieval.visible_chunks(user, course=course, resource_type="past_question")[:60]))
    if not chunks:
        raise ValidationError(f"There are no readable past questions for {course.code} yet.")
    if not enabled():
        raise ValidationError("Past question insights need NexAI's writing model, which isn't set up on this server.")
    prompt = (f"Sources (past exam and test questions for {course.code}):\n{_context(chunks)}\n\n"
              "Which topics appear most often across these past questions? List the topics from most to least frequent, "
              "roughly how often each appears, and the kind of question asked. End with a short revision tip. Cite sources.")
    answer, tin, tout = llm.complete(system=SYSTEM, messages=[{"role": "user", "content": prompt}], max_tokens=1000)
    _record(user, Usage.Mode.INSIGHTS, True, tin, tout)
    return {"answer": answer, "sources": _sources(chunks)}

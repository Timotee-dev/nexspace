"""Keyword retrieval (BM25) over the passages a user is allowed to see.

Only resources from the user's own department that are live (not removed or hidden)
are ever searched, so NexAI can never quote material the user couldn't open.
"""
import math
import re
from collections import Counter

from django.db.models import Q

from .models import ResourceChunk

STOPWORDS = set("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only or other
our out over own same she should so some such than that the their them then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your yours please
explain tell give show list what's whats topics topic question questions csc""".split())
TOKEN = re.compile(r"[a-z0-9]+")
K1, B = 1.4, 0.75


def tokenize(text):
    return [t for t in TOKEN.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


def visible_chunks(user, *, course=None, resource=None, resource_type=None):
    qs = ResourceChunk.objects.filter(
        resource__course__department_id=user.department_id, resource__is_removed=False, resource__is_hidden=False,
    ).select_related("resource__course", "resource__session")
    if course is not None:
        qs = qs.filter(resource__course=course)
    if resource is not None:
        qs = qs.filter(resource=resource)
    if resource_type:
        qs = qs.filter(resource__resource_type=resource_type)
    return qs


def search(user, query, *, course=None, k=6, candidates=500):
    terms = tokenize(query)
    if not terms:
        return []
    prefilter = Q()
    for term in sorted(set(terms), key=len, reverse=True)[:8]:
        prefilter |= Q(text__icontains=term)
    pool = list(visible_chunks(user, course=course).filter(prefilter)[:candidates])
    if not pool:
        return []
    docs = [Counter(tokenize(c.text)) for c in pool]
    lengths = [sum(d.values()) or 1 for d in docs]
    avg = sum(lengths) / len(lengths)
    n = len(pool)
    df = {t: sum(1 for d in docs if t in d) for t in set(terms)}
    scored = []
    for chunk, doc, length in zip(pool, docs, lengths):
        score = 0.0
        for t in set(terms):
            tf = doc.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg))
        # small boost when the question mentions the course code
        code = chunk.resource.course.code.lower().replace(" ", "")
        if code in query.lower().replace(" ", ""):
            score *= 1.2
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda s: -s[0])
    return [c for _, c in scored[:k]]

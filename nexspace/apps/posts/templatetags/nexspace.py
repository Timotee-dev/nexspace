import re

from django import template
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.html import urlize as _urlize
from django.utils.safestring import mark_safe

register = template.Library()
MENTION_HTML_RE = re.compile(r"(?<![\w@/])@([a-z0-9_]{3,30})\b", re.IGNORECASE)


@register.filter
def render_body(text):
    """Escape user text, link URLs (nofollow) and @mentions, keep line breaks."""
    if not text:
        return ""
    html = _urlize(text, nofollow=False, autoescape=True)
    html = html.replace('<a href=', '<a target="_blank" rel="nofollow noopener noreferrer" href=')

    def _mention(match):
        username = match.group(1).lower()
        return f'<a class="mention" href="{reverse("accounts:profile", args=[username])}">@{escape(username)}</a>'

    # Only replace mentions outside of existing anchor tags
    parts = re.split(r"(<a [^>]*>.*?</a>)", html)
    html = "".join(p if p.startswith("<a ") else MENTION_HTML_RE.sub(_mention, p) for p in parts)
    paragraphs = [p.replace("\n", "<br>") for p in re.split(r"\n{2,}", html.strip())]
    return mark_safe("".join(f"<p>{p}</p>" for p in paragraphs))


@register.filter
def ago(value):
    """Compact relative time: now, 5m, 3h, 2d, then a short date."""
    if not value:
        return ""
    seconds = (timezone.now() - value).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d"
    local = timezone.localtime(value)
    label = f"{local.day} {local:%b}"
    return label if local.year == timezone.localtime().year else f"{label} {local.year}"


@register.filter
def compact(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return n
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(n) >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


@register.filter
def filesize(n):
    n = int(n or 0)
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{max(n // 1024, 1)} KB"


@register.filter
def percent(part, whole):
    try:
        return round(100 * int(part) / int(whole)) if int(whole) else 0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if hasattr(mapping, "get") else None

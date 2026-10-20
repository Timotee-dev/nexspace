import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()
CITE = re.compile(r"\[(\d{1,2})\]")
BOLD = re.compile(r"\*\*(.+?)\*\*")


@register.filter
def render_answer(text, prefix="src"):
    """Safely render a NexAI answer: escape everything, then add bullets, bold and citation links."""
    if not text:
        return ""
    html_parts, bullets = [], []

    def flush():
        if bullets:
            html_parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in escape(text).split("\n"):
        line = raw.strip()
        line = BOLD.sub(r"<strong>\1</strong>", line)
        line = CITE.sub(rf'<a class="cite" href="#{prefix}-\1">[\1]</a>', line)
        if re.match(r"^[-•*]\s+", line):
            bullets.append(re.sub(r"^[-•*]\s+", "", line))
            continue
        flush()
        if line:
            html_parts.append(f"<p>{line}</p>")
    flush()
    return mark_safe("".join(html_parts))

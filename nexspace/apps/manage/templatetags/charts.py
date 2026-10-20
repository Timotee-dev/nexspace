from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def bar_chart(series, label="", height=90):
    """Small accessible inline SVG bar chart (no JS, no external library)."""
    values = [row["n"] for row in series]
    peak = max(values) or 1
    n = len(values)
    width = n * 10
    bars = []
    for i, row in enumerate(series):
        h = 0 if not row["n"] else max(2, round(row["n"] / peak * (height - 4)))
        bars.append(f'<rect x="{i * 10 + 1}" y="{height - h}" width="8" height="{h}" rx="2">'
                    f'<title>{row["day"]:%d %b}: {row["n"]}</title></rect>')
    total = sum(values)
    summary = escape(f"{label}: {total} over the last {n} days, peak {max(values)} on "
                     f"{series[values.index(max(values))]['day']:%d %b}" if total else f"{label}: none in the last {n} days")
    return mark_safe(
        f'<svg class="bar-chart" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'aria-label="{summary}">{"".join(bars)}</svg>'
    )

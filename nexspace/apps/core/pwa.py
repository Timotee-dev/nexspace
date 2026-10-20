"""Progressive Web App endpoints: manifest, service worker and offline page."""
import json

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static

SW_VERSION = "nexspace-v5"


def manifest_view(request):
    data = {
        "name": "NexSpace",
        "short_name": "NexSpace",
        "description": "The digital home of your department.",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#121a2e",
        "theme_color": "#121a2e",
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Create a post", "url": "/compose/"},
            {"name": "Calendar", "url": "/calendar/"},
            {"name": "Notifications", "url": "/notifications/"},
        ],
    }
    response = HttpResponse(json.dumps(data), content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def service_worker_view(request):
    precache = [
        "/offline/",
        static("css/app.css"), static("js/app.js"), static("js/feed.js"), static("js/composer.js"),
        static("img/icon-192.png"), static("img/icon-512.png"),
    ]
    body = render_to_string("pwa/sw.js", {"version": SW_VERSION, "precache": json.dumps(precache)})
    response = HttpResponse(body, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def offline_view(request):
    return render(request, "pwa/offline.html")

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core import pwa
from apps.notifications.urls import api_urlpatterns as notification_api
from apps.search.urls import api_urlpatterns as search_api

api_patterns = [
    path("auth/", include("apps.accounts.api_urls")),
    path("profiles/", include("apps.accounts.profile_api_urls")),
    path("academics/", include("apps.academics.api_urls")),
    path("topics/", include("apps.topics.api_urls")),
    path("", include("apps.posts.api_urls")),
    path("", include(notification_api)),
    path("", include(search_api)),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/", include(api_patterns)),
    path("", include("apps.accounts.urls")),
    path("", include("apps.social.urls")),
    path("", include("apps.spaces.urls")),
    path("", include("apps.resources.urls")),
    path("", include("apps.notices.urls")),
    path("", include("apps.moderation.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.search.urls")),
    path("", include("apps.discover.urls")),
    path("", include("apps.groups.urls")),
    path("", include("apps.manage.urls")),
    path("", include("apps.nexai.urls")),
    path("manifest.webmanifest", pwa.manifest_view, name="manifest"),
    path("sw.js", pwa.service_worker_view, name="service-worker"),
    path("offline/", pwa.offline_view, name="offline"),
    path("", include("apps.posts.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

"""NexSpace settings.

Everything environment-specific comes from environment variables (see .env.example).
With no .env at all, the project runs locally on SQLite, console email and local media.
"""
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", True)
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("SECRET_KEY must be set when DEBUG is False.")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "apps.core",
    "apps.academics",
    "apps.topics",
    "apps.accounts",
    "apps.social",
    "apps.reputation",
    "apps.posts",
    "apps.spaces",
    "apps.resources",
    "apps.notices",
    "apps.moderation",
    "apps.notifications",
    "apps.search",
    "apps.discover",
    "apps.groups",
    "apps.manage",
    "apps.nexai",
    "apps.messaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.OnboardingMiddleware",
    "apps.accounts.middleware.LastSeenMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.nexspace",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, conn_health_checks=True,
                                                  ssl_require=not DEBUG)}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# --- Static & media ------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# File storage, in order of preference:
#   1. Supabase Storage (private bucket, signed links, up to 50 MB per file on the free plan)
#   2. Cloudinary (public links, 10 MB per file on the free plan)
#   3. The local disk (development only — Render wipes it on every deploy)
SUPABASE_S3_ENDPOINT = os.environ.get("SUPABASE_S3_ENDPOINT", "").strip().rstrip("/")
SUPABASE_S3_REGION = os.environ.get("SUPABASE_S3_REGION", "").strip()
SUPABASE_S3_ACCESS_KEY_ID = os.environ.get("SUPABASE_S3_ACCESS_KEY_ID", "").strip()
SUPABASE_S3_SECRET_ACCESS_KEY = os.environ.get("SUPABASE_S3_SECRET_ACCESS_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "nexspace").strip()
SUPABASE_STORAGE = all([SUPABASE_S3_ENDPOINT, SUPABASE_S3_REGION, SUPABASE_S3_ACCESS_KEY_ID,
                        SUPABASE_S3_SECRET_ACCESS_KEY])
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()
if SUPABASE_STORAGE:
    FILE_STORAGE_NAME = "Supabase Storage"
    _default_storage = "apps.core.storage.SupabaseStorage"
elif CLOUDINARY_URL:
    FILE_STORAGE_NAME = "Cloudinary"
    _default_storage = "apps.core.storage.CloudinaryStorage"
else:
    FILE_STORAGE_NAME = ""
    _default_storage = "django.core.files.storage.FileSystemStorage"
STORAGES = {
    "default": {"BACKEND": _default_storage},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Upload limits (Section 39 of the spec). Cloudinary's free plan rejects files over 10 MB.
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_MB = int(os.environ.get("MAX_DOCUMENT_MB", "50" if SUPABASE_STORAGE else "10"))
MAX_DOCUMENT_UPLOAD_BYTES = MAX_DOCUMENT_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_DOCUMENT_UPLOAD_BYTES + 1024 * 1024

# --- Email ---------------------------------------------------------------
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
EMAIL_BACKEND = (
    "apps.core.email.BrevoEmailBackend" if BREVO_API_KEY else "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "NexSpace <no-reply@example.com>")

EMAIL_VERIFICATION_MAX_AGE = timedelta(days=3)

# --- Cache (used by login rate limiting) ---------------------------------
# With a real database, use it as a shared cache so login lockouts and rate limits hold across
# all Gunicorn workers. The table is created by a migration (apps/core/migrations/0002).
if DATABASE_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "nexspace_cache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nexspace"}}

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

# --- REST framework ------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.api.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min", "user": "240/min", "auth": "10/min"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NexSpace API",
    "DESCRIPTION": "API for NexSpace — the digital home of the department.",
    "VERSION": "0.10.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "PostKindEnum": "apps.posts.models.Post.Kind",
        "NotificationKindEnum": "apps.notifications.models.Notification.Kind",
    },
}

# --- Security ------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # the JS fetch helper reads the CSRF token from the cookie
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}

# --- Web Push (optional) -------------------------------------------------
# Generate with: python manage.py generate_vapid_keys
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com").strip()
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Uploaded images are resized to this longest edge and stripped of metadata (EXIF/GPS).
IMAGE_MAX_EDGE = 1600

# --- NexAI (optional) ------------------------------------------------------
# Without ANTHROPIC_API_KEY, NexAI still searches course materials and shows the
# matching passages; it just doesn't write an answer.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
NEXAI_MODEL = os.environ.get("NEXAI_MODEL", "claude-haiku-4-5-20251001").strip()
NEXAI_DAILY_LIMIT = int(os.environ.get("NEXAI_DAILY_LIMIT", "40"))
NEXAI_ENABLED = bool(ANTHROPIC_API_KEY)

# Secret for /internal/run-scheduled/ (free alternative to a paid Render cron job).
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

# --- Platform ownership ------------------------------------------------------
# These accounts are always platform (super) admins once their email is verified. They can't be
# demoted, suspended, banned or deleted from inside NexSpace. Override with a comma-separated list.
PLATFORM_OWNER_EMAILS = [e.lower() for e in env_list("PLATFORM_OWNER_EMAILS", "arifalotimothy@gmail.com")]
# Who verifies staff sign-ups: "platform" (platform admins only) or "department" (HOD/department admins).
STAFF_VERIFICATION = os.environ.get("STAFF_VERIFICATION", "platform").strip().lower()

# Stay signed in for 30 days.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

# --- Email digests ---------------------------------------------------------------
# Brevo's free plan sends 300 emails a day; digests stop at this cap and continue on the next run.
DIGEST_DAILY_CAP = int(os.environ.get("DIGEST_DAILY_CAP", "250"))
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "7"))  # local time digests start going out

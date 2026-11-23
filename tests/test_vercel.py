"""Running on Vercel: settings, the visitor's address, the scheduled-jobs URL and the build script."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.core.net import client_ip

ROOT = Path(__file__).resolve().parent.parent

VERCEL_ENV = {
    "VERCEL": "1", "VERCEL_ENV": "production", "SECRET_KEY": "x" * 50,
    "VERCEL_PROJECT_PRODUCTION_URL": "nexspace.vercel.app", "VERCEL_URL": "nexspace-abc123.vercel.app",
    "DATABASE_URL": "postgresql://u:p@aws-0-eu.pooler.supabase.com:6543/postgres",
}


def settings_on_vercel(**extra):
    """Load the real settings in a fresh Python with Vercel's environment and return the values we check."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "RENDER", "DEBUG", "SITE_URL",
                                                                     "DATABASE_URL", "MAX_UPLOAD_MB", "CLOUDINARY",
                                                                     "DIRECT_UPLOADS"))}
    env.update(VERCEL_ENV, DJANGO_SETTINGS_MODULE="config.settings", **extra)
    code = ("import json; from django.conf import settings as s; db = s.DATABASES['default']; print(json.dumps({"
            "'debug': s.DEBUG, 'hosts': s.ALLOWED_HOSTS, 'csrf': s.CSRF_TRUSTED_ORIGINS, 'site': s.SITE_URL,"
            "'max_age': db['CONN_MAX_AGE'], 'cursors': db.get('DISABLE_SERVER_SIDE_CURSORS', False),"
            "'prepare': db.get('OPTIONS', {}).get('prepare_threshold', 'unset'), 'media': str(s.MEDIA_ROOT),"
            "'upload': s.MAX_UPLOAD_MB, 'request': s.MAX_REQUEST_MB, 'host': s.HOSTING_NAME, 'direct': s.DIRECT_UPLOADS}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_settings_on_vercel():
    s = settings_on_vercel()
    assert s["debug"] is False  # never debug pages in production, even if DEBUG isn't set
    assert {"nexspace.vercel.app", "nexspace-abc123.vercel.app"} <= set(s["hosts"])
    assert "https://nexspace.vercel.app" in s["csrf"]
    assert s["site"] == "https://nexspace.vercel.app"  # emails link to the production address
    assert s["max_age"] == 0  # no connections left open by short-lived functions
    assert s["cursors"] is True and s["prepare"] is None  # Supabase transaction pooler (port 6543)
    assert s["media"] == "/tmp/media"  # the only writable folder there
    assert s["upload"] == 4 and s["request"] == 4  # Vercel refuses requests over 4.5 MB
    assert s["host"] == "Vercel" and s["direct"] is False  # no Cloudinary: files travel with the form


def test_vercel_with_cloudinary_uploads_straight_to_it():
    s = settings_on_vercel(CLOUDINARY_URL="cloudinary://123:abc@demo")
    assert s["direct"] is True and s["upload"] == 10  # Cloudinary's free-plan limit per file, not Vercel's 4.5 MB
    s = settings_on_vercel(CLOUDINARY_URL="cloudinary://123:abc@demo", DIRECT_UPLOADS="false")
    assert s["direct"] is False and s["upload"] == 4


def test_vercel_settings_can_be_overridden():
    s = settings_on_vercel(SITE_URL="https://nexspace.unimed.edu.ng/", ALLOWED_HOSTS="nexspace.unimed.edu.ng",
                           DATABASE_URL="postgresql://u:p@db.example.supabase.co:5432/postgres", MAX_UPLOAD_MB="8")
    assert s["site"] == "https://nexspace.unimed.edu.ng"
    assert "nexspace.unimed.edu.ng" in s["hosts"] and "nexspace.vercel.app" in s["hosts"]
    assert s["cursors"] is False and s["prepare"] == "unset"  # direct connection: nothing to switch off
    assert s["upload"] == 8


def test_render_settings_unchanged():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "DEBUG", "DATABASE_URL"))}
    env.update(RENDER="true", SECRET_KEY="x" * 50, DJANGO_SETTINGS_MODULE="config.settings",
               DATABASE_URL="postgresql://u:p@db.example.com:5432/postgres")
    code = ("import json; from django.conf import settings as s; db = s.DATABASES['default']; print(json.dumps("
            "[s.DEBUG, db['CONN_MAX_AGE'], s.MAX_UPLOAD_MB, s.MAX_REQUEST_MB, s.HOSTING_NAME]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [False, 600, 100, 0, "Render"]


def test_visitor_address_on_vercel_ignores_fake_cloudflare_headers(settings):
    settings.ON_VERCEL = True
    request = RequestFactory().get("/", HTTP_CF_CONNECTING_IP="9.9.9.9", HTTP_X_VERCEL_FORWARDED_FOR="41.58.1.2",
                                   HTTP_X_REAL_IP="41.58.1.2", REMOTE_ADDR="10.0.0.1")
    assert client_ip(request) == "41.58.1.2"
    assert client_ip(RequestFactory().get("/", HTTP_X_REAL_IP="41.58.1.3")) == "41.58.1.3"


def test_visitor_address_off_vercel_unchanged(settings):
    settings.ON_VERCEL = False
    request = RequestFactory().get("/", HTTP_CF_CONNECTING_IP="9.9.9.9", HTTP_X_VERCEL_FORWARDED_FOR="41.58.1.2")
    assert client_ip(request) == "9.9.9.9"


@pytest.mark.django_db
def test_vercel_cron_runs_scheduled_jobs(client, settings):
    """Vercel Cron sends `Authorization: Bearer <CRON_SECRET>` to the path in vercel.json."""
    config = json.loads((ROOT / "vercel.json").read_text())
    path = config["crons"][0]["path"]
    assert path == reverse("core:run-scheduled")  # same URL, with its trailing slash (cron doesn't follow redirects)
    settings.CRON_SECRET = "s3cret-value-1234"
    assert client.get(path).status_code == 404
    assert client.get(path, HTTP_AUTHORIZATION="Bearer wrong").status_code == 404
    response = client.get(path, HTTP_AUTHORIZATION="Bearer s3cret-value-1234")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_vercel_config_files():
    config = json.loads((ROOT / "vercel.json").read_text())
    assert config["functions"]["config/wsgi.py"]["maxDuration"] <= 300  # Hobby plan maximum
    assert config["crons"][0]["schedule"] == "0 5 * * *"  # Hobby plan: once a day at most
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'entrypoint = "config.wsgi:application"' in pyproject and "psycopg[binary]" in pyproject
    required = {line.split(">")[0].split("<")[0].split("[")[0].strip().lower()
                for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip() and not line.startswith("#")}
    for name in required:  # Vercel installs from pyproject.toml: nothing the app needs may be missing
        assert f'"{name}' in pyproject.lower(), name


def test_build_script_only_migrates_production(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "DEBUG", "DATABASE_URL"))}
    db = tmp_path / "build.sqlite3"
    env.update(VERCEL="1", VERCEL_ENV="preview", SECRET_KEY="x" * 50, DATABASE_URL=f"sqlite:///{db}",
               ALLOWED_HOSTS="nexspace.vercel.app", DEBUG="1")  # DEBUG: SQLite here can't take Postgres SSL options
    out = subprocess.run([sys.executable, "vercel_build.py"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Skipping migrations" in out.stdout and not db.exists()
    env["VERCEL_ENV"] = "production"
    out = subprocess.run([sys.executable, "vercel_build.py"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr + out.stdout
    assert db.exists()

"""
Bordo 백엔드 설정.

개발 편의를 위해 기본값은 SQLite 이지만, DATABASE_URL 이 있으면 PostgreSQL 을 씁니다.
운영에서는 pgvector 가 필요하므로 PostgreSQL 을 전제로 합니다.
"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "apps.common",
    "apps.accounts",
    "apps.orgs",
    "apps.agent",
    "apps.meetings",
    "apps.home",
    "apps.chat",
    "apps.states",
    "apps.tasks",
    "apps.calendars",
    "apps.documents",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.RequestIdMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ─────────────────────────────────────────── DB
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    from urllib.parse import urlparse
    u = urlparse(_db_url)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": u.path.lstrip("/"), "USER": u.username, "PASSWORD": u.password,
        "HOST": u.hostname, "PORT": u.port or 5432,
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"        # 저장은 UTC, 표시는 사용자 timezone 으로 환산
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─────────────────────────────────────────── DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.bordo_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_HEADERS = list(__import__("corsheaders.defaults", fromlist=["default_headers"]).default_headers) + [
    "idempotency-key", "x-service-token",
]

# ─────────────────────────────────────────── Bordo 고유 설정
BORDO = {
    "SOFT_DELETE_GRACE_DAYS": 30,       # 소프트 삭제 복구 유예
    "CHAT_EDIT_WINDOW_MINUTES": 15,     # 메시지 수정 제한
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_HOPS": 3,                      # AI↔AI 무한 대화 차단
    "SERVICE_TOKEN": os.environ.get("BORDO_SERVICE_TOKEN", "dev-service-token"),
}

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


def _csv(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


# 개발에서는 그대로 열어 두고, 배포에서는 DJANGO_ALLOWED_HOSTS 로 좁힙니다.
# 도메인이 붙은 뒤에도 "*" 로 두면 Host 헤더를 위조한 요청이 그대로 들어옵니다.
# 배포에서 비어 있으면 Django 가 모든 요청을 400 으로 막습니다 — 조용히 열려 있는
# 것보다 눈에 띄게 막히는 편이 낫습니다.
ALLOWED_HOSTS = _csv("DJANGO_ALLOWED_HOSTS") or (["*"] if DEBUG else [])

# Cloudflare Tunnel 뒤에 있으면 Django 는 요청을 http 로 봅니다. 이 값이 없으면
# 리다이렉트가 http 로 나가고 admin 로그인이 순환합니다.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# admin·세션 폼은 Origin 을 대조합니다. 터널 도메인을 넣지 않으면 로그인이 403 입니다.
CSRF_TRUSTED_ORIGINS = _csv("DJANGO_CSRF_TRUSTED_ORIGINS")


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


# 쿠키에 Secure 를 붙입니다. 배포는 터널이 TLS 를 끊어 주므로 기본으로 켜고,
# 개발은 http 라 켜면 로그인이 안 됩니다.
SESSION_COOKIE_SECURE = _flag("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# HSTS 는 기본 0(끔)입니다. 이 도메인은 다른 프로젝트가 쓰는 도메인의 하위입니다.
# 켤 때도 SECURE_HSTS_INCLUDE_SUBDOMAINS 는 절대 켜지 마십시오 — 형제 서비스까지
# HTTPS 전용으로 묶여 버리고, 브라우저에 캐시되면 되돌리는 데 시간이 걸립니다.
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Cloudflare 가 이미 http→https 로 돌려보냅니다. Django 에서 또 켜면 루프백으로
# 들어오는 헬스체크까지 301 이 되어 배포 확인이 실패합니다. 필요하면 켤 수 있게만 둡니다.
SECURE_SSL_REDIRECT = _flag("DJANGO_SSL_REDIRECT", False)

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
    from urllib.parse import urlparse, unquote
    u = urlparse(_db_url)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": u.path.lstrip("/"),
        # 생성한 비밀번호에 @ · / · # 이 들어가면 URL 인코딩해서 넣게 됩니다.
        # 디코딩하지 않으면 인증만 조용히 실패하고 원인이 드러나지 않습니다.
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname, "PORT": u.port or 5432,
        # 요청마다 연결을 새로 여는 비용이 라즈베리파이에서는 눈에 띕니다.
        # 0 이면 매 요청 새 연결 — 개발에서는 그 편이 편하므로 환경변수로 둡니다.
        "CONN_MAX_AGE": int(os.environ.get("DJANGO_CONN_MAX_AGE", "60")),
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
# collectstatic 이 모아 둘 위치. 없으면 배포에서 collectstatic 이 ImproperlyConfigured 로
# 죽고, 넘어가더라도 admin 이 CSS 없이 뜹니다. 개발에서는 쓰이지 않습니다.
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT") or BASE_DIR / "staticfiles")
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

# 프론트가 별도 저장소·별도 오리진이라 CORS 가 필요합니다.
# 개발에서는 열어 두고, 배포에서는 DJANGO_CORS_ALLOWED_ORIGINS 로 프론트 주소만 허용합니다.
# 목록을 넣으면 ALLOW_ALL 은 자동으로 꺼집니다.
CORS_ALLOWED_ORIGINS = _csv("DJANGO_CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = not CORS_ALLOWED_ORIGINS
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

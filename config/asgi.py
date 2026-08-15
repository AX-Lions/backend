"""
ASGI 진입점.

HTTP 와 WebSocket 을 함께 받습니다. gunicorn(WSGI)으로 띄우면 HTTP 만 되고
실시간은 안 됩니다 — 실시간을 쓰려면 daphne 나 uvicorn 으로 이 파일을 띄우십시오.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application

# Django 앱이 먼저 올라와야 아래 import 가 모델을 찾습니다.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.agent.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # AuthMiddlewareStack 을 쓰지 않습니다. 세션 쿠키가 아니라 JWT 로 붙고,
    # 검증은 컨슈머가 직접 합니다.
    "websocket": URLRouter(websocket_urlpatterns),
})

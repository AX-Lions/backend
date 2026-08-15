"""WebSocket 라우팅. HTTP 는 config/urls.py, 실시간은 여기입니다."""
from django.urls import path

from .consumers import ProjectConsumer

websocket_urlpatterns = [
    path("ws/projects/<uuid:project_id>", ProjectConsumer.as_asgi()),
]

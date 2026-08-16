from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agent"
    label = "agent"

    def ready(self):
        # 작업 플로우 화살표는 남의 앱 모델을 관찰해 그립니다.
        # 여기서 붙이지 않으면 조용히 아무 화살표도 안 생깁니다.
        from . import signals
        signals.connect()

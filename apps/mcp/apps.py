from django.apps import AppConfig


class McpConfig(AppConfig):
    name = "apps.mcp"
    label = "mcp"
    verbose_name = "14. MCP (개인 AI)"

    def ready(self):
        # 도구는 import 시점에 레지스트리에 들어갑니다. 여기서 한 번 불러 두지 않으면
        # 첫 요청 전에는 tools/list 가 비어 있습니다.
        from .tools import registry  # noqa: F401

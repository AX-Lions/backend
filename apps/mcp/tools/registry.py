"""
이름 → 도구.

클라이언트가 이름 문자열로 부르므로 등록 지점을 하나로 둡니다. 같은 이름을 두 번
등록하면 나중 것이 조용히 이기는데, 그 상태로 배포되면 "고쳤는데 안 바뀐다"가
되므로 등록 시점에 막습니다.
"""
from __future__ import annotations

from .base import McpTool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, McpTool] = {}

    def register(self, tool: McpTool) -> McpTool:
        if tool.name in self._tools:
            raise ValueError(f"MCP 도구 이름이 중복됩니다: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> McpTool | None:
        return self._tools.get(name)

    def list(self) -> list[McpTool]:
        return list(self._tools.values())

    def catalog(self) -> list[dict]:
        return [t.to_spec() for t in self.list()]


registry = ToolRegistry()

# 도구는 import 되는 순간 등록됩니다.
from . import write  # noqa: E402,F401

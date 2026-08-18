"""
MCP 도구의 뼈대.

대리인 스킬(`apps/agent/services/skills`)과 모양은 거의 같지만 **레지스트리를
공유하지 않습니다.** 신뢰 모델이 다릅니다 — 스킬은 남의 질문에 본인을 대리하며
`principal ≠ actor` 이고 비공개 자료를 걸러야 하지만, MCP 도구는 **항상 본인**이
본인 토큰으로 본인 것을 씁니다. 합치면 대리인 규칙을 고칠 때 MCP 가 조용히 따라
바뀝니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolContext:
    user: Any
    request: Any = None


@dataclass
class ToolResult:
    """
    `tools/call` 의 result 로 그대로 직렬화됩니다.

    `text` 는 사람이 읽을 한 줄이고 `structured` 가 프로그램이 읽을 본문입니다.
    도구 실행 실패는 JSON-RPC `error` 가 아니라 **`is_error=True`** 입니다 — `error`
    로 내면 클라이언트가 프로토콜 문제로 취급해 모델에게 안 보여주고, AI 는 왜
    실패했는지 모른 채 같은 호출을 반복합니다.
    """
    text: str
    structured: dict | None = None
    is_error: bool = False

    @classmethod
    def fail(cls, text: str, **structured) -> "ToolResult":
        return cls(text=text, structured=structured or None, is_error=True)

    def to_rpc(self) -> dict:
        body: dict = {"content": [{"type": "text", "text": self.text}],
                      "isError": self.is_error}
        if self.structured is not None:
            body["structuredContent"] = self.structured
        return body


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:   # pragma: no cover
        raise NotImplementedError

    def to_spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


# ── 인자 검사 — 도구 안에서 반복되는 것만 ──────────────────────
def arg_str(args: dict, key: str, *, required=False, max_len=None, default="") -> str:
    from config.errors import BordoError
    value = args.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise BordoError("VALIDATION_ERROR", f"{key} 은(는) 문자열이어야 합니다.",
                         details={key: value})
    value = value.strip()
    if required and not value:
        raise BordoError("VALIDATION_ERROR", f"{key} 은(는) 필수입니다.")
    if max_len and len(value) > max_len:
        raise BordoError("VALIDATION_ERROR", f"{key} 은(는) {max_len}자 이내여야 합니다.",
                         details={key: value[:40] + "…"})
    return value

"""
JSON-RPC 처리 — dual-era.

MCP 명세는 `2025-11-25` 까지가 `initialize` 핸드셰이크 방식(legacy)이고, `2026-07-28`
부터는 `initialize` 가 없어지고 요청마다 `_meta` 에 판 번호를 싣습니다(modern).
한쪽만 만들면 어느 클라이언트를 쓰느냐에 따라 연결 자체가 안 되므로 둘 다 받습니다.
분기 지점은 여기 한 곳입니다.

    본문 params._meta 에 io.modelcontextprotocol/protocolVersion 이 있다  → modern
    없다                                                                 → legacy

**세션을 만들지 않습니다.** 최신 판에서 세션이 삭제됐고, 인증이 Bearer 토큰이라
요청마다 사용자를 압니다 — 워커 여러 개로 늘려도 세션 공유를 신경 쓸 게 없습니다.
**SSE 도 없습니다.** 도구가 전부 짧은 쓰기라 스트리밍할 게 없고, 명세는 단일 JSON
응답을 허용합니다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from config.errors import BordoError

from .tools.base import ToolContext, ToolResult
from .tools.projects import instructions_for
from .tools.registry import registry

logger = logging.getLogger("bordo.mcp")

SERVER_INFO = {"name": "bordo", "version": "1.0.0"}
LEGACY_VERSION = "2025-11-25"
MODERN_VERSION = "2026-07-28"
SUPPORTED_VERSIONS = [MODERN_VERSION, LEGACY_VERSION]
META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"

# JSON-RPC 예약 구간 · MCP 가 정한 코드
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022

#: 서버 예외를 그대로 내보내지 않습니다 — Django 원문 오류에는 제약 조건 이름과 값이
#: 섞여 있고, MCP 에서는 그게 개발자 컴퓨터의 AI 대화창에 그대로 찍힙니다.
INTERNAL_ERROR_TEXT = "서버 오류로 처리하지 못했습니다. 잠시 후 다시 시도하십시오."


#: modern 결과 봉투에 붙는 캐시 메타.
#:
#: `2026-07-28` 은 결과마다 **얼마나 캐시해도 되는지**를 서버가 말하게 합니다.
#: 빠뜨리면 클라이언트가 스키마 검증에서 응답을 통째로 버립니다 — 서버는
#: `connected` 로 뜨는데 도구가 0개인 상태가 그것입니다.
#:
#: 둘 다 보수적으로 답합니다.
#:
#: - `ttlMs = 0` — 캐시하지 마십시오. 도구 목록은 고정이지만 `tools/call` 은
#:   **쓰기**라 한 번이라도 재사용되면 기록이 어긋납니다. 목록만 따로 늘려 봐야
#:   아낄 왕복이 연결당 한 번뿐입니다.
#: - `cacheScope = "private"` — 응답이 Bearer 토큰의 주인 기준입니다.
#:   `public` 으로 주면 중간 캐시가 **남의 결과를 나에게** 줄 수 있습니다.
CACHE_META = {"ttlMs": 0, "cacheScope": "private"}


def _modern_result(body: dict) -> dict:
    """modern 결과 봉투. 한 곳에서만 만듭니다 — 나눠 쓰면 한쪽만 고쳐집니다."""
    return {"resultType": "complete", **CACHE_META, **body}


@dataclass
class RpcResponse:
    status: int
    body: dict | None          # None 이면 본문 없음 (알림 → 202)

    @classmethod
    def ok(cls, id_, result):
        return cls(200, {"jsonrpc": "2.0", "id": id_, "result": result})

    @classmethod
    def error(cls, id_, code, message, *, status=200, data=None):
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return cls(status, {"jsonrpc": "2.0", "id": id_, "error": err})

    @classmethod
    def accepted(cls):
        return cls(202, None)


# ─────────────────────────────────────────── 진입점
def handle(request) -> RpcResponse:
    """인증이 끝난 요청을 받아 응답 하나를 만듭니다. 예외를 밖으로 던지지 않습니다."""
    try:
        msg = json.loads(request.body or b"")
    except (ValueError, UnicodeDecodeError):
        return RpcResponse.error(None, PARSE_ERROR, "JSON 을 읽을 수 없습니다.")

    if isinstance(msg, list):
        # 배치는 2025-06-18 에서 제거됐습니다. 받는 척하면 절반만 처리되는 상태가 됩니다.
        return RpcResponse.error(None, INVALID_REQUEST, "배치 요청은 지원하지 않습니다.")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" \
            or not isinstance(msg.get("method"), str):
        return RpcResponse.error(None, INVALID_REQUEST,
                                 "jsonrpc: '2.0' 과 method 가 있어야 합니다.")

    id_ = msg.get("id")
    method = msg["method"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return RpcResponse.error(id_, INVALID_PARAMS, "params 는 객체여야 합니다.")

    meta = params.get("_meta") or {}
    body_version = meta.get(META_VERSION_KEY) if isinstance(meta, dict) else None
    ctx = ToolContext(user=request.user, request=request)

    if body_version is not None:
        return _modern(request, id_, method, params, body_version, ctx)
    return _legacy(id_, method, params, ctx)


# ─────────────────────────────────────────── modern (2026-07-28)
def _modern(request, id_, method, params, body_version, ctx) -> RpcResponse:
    if body_version not in SUPPORTED_VERSIONS:
        return RpcResponse.error(id_, UNSUPPORTED_VERSION, "지원하지 않는 프로토콜 판입니다.",
                                 status=400, data={"supported": SUPPORTED_VERSIONS})

    # 헤더와 본문이 같아야 합니다. 로드밸런서가 헤더로 라우팅하고 서버가 본문으로
    # 실행하면 서로 다른 걸 볼 수 있어, 명세가 검증을 서버 의무로 뒀습니다.
    mismatch = _header_mismatch(request, method, params, body_version)
    if mismatch:
        return RpcResponse.error(id_, HEADER_MISMATCH, "헤더와 본문이 다릅니다.",
                                 status=400, data=mismatch)

    if method == "server/discover":
        return RpcResponse.ok(id_, _modern_result({
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": {"tools": {}},
            "instructions": instructions_for(ctx.user),
            "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
        }))
    if method == "ping":
        return RpcResponse.ok(id_, {})
    if method == "tools/list":
        # `resultType` 은 **modern 에서 필수**입니다.
        #
        # 이걸 빠뜨리면 Claude Code 가 목록을 통째로 거부해 `/mcp` 에 서버는
        # `connected` 로 뜨는데 **도구가 0개**입니다. 붙은 것처럼 보여서 원인을
        # 찾기가 더 어렵습니다. `server/discover` 에만 넣어 뒀던 것이 문제였습니다.
        #
        # 페이지를 나누지 않으므로 언제나 `complete` 입니다 — 도구가 셋이라
        # 나눌 이유가 없고, `partial` 로 주면 클라이언트가 커서를 들고 한 번 더
        # 묻는데 줄 것이 없습니다.
        return RpcResponse.ok(id_, _modern_result({"tools": registry.catalog()}))
    if method == "tools/call":
        return _call(id_, params, ctx, modern=True)
    if method.startswith("notifications/"):
        return RpcResponse.accepted()
    # modern 에서는 모르는 method 가 HTTP 404 입니다 — 구현 안 된 서버와 구별하는 규칙.
    return RpcResponse.error(id_, METHOD_NOT_FOUND, f"지원하지 않는 method: {method}",
                             status=404)


def _header_mismatch(request, method, params, body_version) -> dict | None:
    checks = {
        "MCP-Protocol-Version": (request.META.get("HTTP_MCP_PROTOCOL_VERSION"), body_version),
        "Mcp-Method": (request.META.get("HTTP_MCP_METHOD"), method),
    }
    if method == "tools/call":
        checks["Mcp-Name"] = (request.META.get("HTTP_MCP_NAME"), params.get("name"))
    for header, (given, expected) in checks.items():
        if given is not None and given != expected:
            return {"header": header, "header_value": given, "body_value": expected}
    return None


# ─────────────────────────────────────────── legacy (≤ 2025-11-25)
def _legacy(id_, method, params, ctx) -> RpcResponse:
    if method == "initialize":
        return RpcResponse.ok(id_, {
            "protocolVersion": LEGACY_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": instructions_for(ctx.user),
        })
    if method.startswith("notifications/"):
        # `notifications/initialized` 를 안 받으면 연결이 안 됩니다. 응답은 202 + 본문 없음.
        return RpcResponse.accepted()
    if method == "ping":
        return RpcResponse.ok(id_, {})
    if method == "tools/list":
        return RpcResponse.ok(id_, {"tools": registry.catalog()})
    if method == "tools/call":
        return _call(id_, params, ctx)
    return RpcResponse.error(id_, METHOD_NOT_FOUND, f"지원하지 않는 method: {method}")


# ─────────────────────────────────────────── tools/call
def _call(id_, params, ctx, *, modern=False) -> RpcResponse:
    name = params.get("name")
    tool = registry.get(name) if isinstance(name, str) else None
    if tool is None:
        return RpcResponse.error(id_, INVALID_PARAMS, f"없는 도구입니다: {name}",
                                 data={"available": [t.name for t in registry.list()]})
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return RpcResponse.error(id_, INVALID_PARAMS, "arguments 는 객체여야 합니다.")

    # 도구 실행 오류는 JSON-RPC error 가 아니라 result.isError 입니다.
    # error 로 내면 클라이언트가 프로토콜 문제로 취급해 모델에게 안 보여주고,
    # AI 는 왜 실패했는지 모른 채 같은 호출을 반복합니다.
    try:
        result = tool.run(args, ctx)
    except BordoError as e:
        result = ToolResult.fail(e.message, code=e.code, details=e.details)
    except Exception:                                  # noqa: BLE001
        logger.exception("MCP 도구 실패: %s user=%s", name, ctx.user.id)
        result = ToolResult.fail(INTERNAL_ERROR_TEXT)

    body = result.to_rpc()
    if modern:
        body = _modern_result(body)
    return RpcResponse.ok(id_, body)

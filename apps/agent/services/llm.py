"""
LLM 호출.

**프로바이더 지식을 이 파일에 가둡니다.** 스킬도 ReAct 루프도 OpenAI 를 모릅니다.
모델을 바꿀 때 고칠 파일이 하나여야, 스킬 여러 개를 다시 쓰는 일이 없습니다.

밖으로 내보내는 것은 `chat()` 하나입니다.

    chat(messages, tools, system) -> LLMResponse(text, tool_calls, error, usage)

`messages` 는 프로바이더 중립 형식입니다. 변환은 여기서만 합니다.

    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [...]}
    {"role": "tool",      "tool_call_id": "...", "content": "..."}
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger("bordo.agent")

#: 재시도할 만한 일시적 상태. 문자열로 보는 이유는 SDK 예외 계층이 버전마다
#: 바뀌어서, 타입에 의존하면 업그레이드 때 조용히 재시도가 멈추기 때문입니다.
_RETRYABLE = ("429", "500", "502", "503", "504", "overloaded", "rate limit",
              "timeout", "timed out", "temporarily unavailable", "connection")

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0     # 1초 → 2초

#: 한 번의 호출을 기다리는 시간.
#:
#: SDK 기본값은 **600초**입니다. 여기에 위 재시도 3회가 곱해지면 한 번의
#: 대리인 호출이 30분을 매답니다. 게다가 개발 기본값이
#: `CELERY_TASK_ALWAYS_EAGER=1` 이라 이 대기가 **봇의 HTTP 요청 스레드 안에서**
#: 일어납니다 — 회의 중 OpenAI 가 느려지면 봇이 통째로 멈춥니다.
#:
#: 회의는 사람이 말하는 속도로 흘러갑니다. 20초를 넘겨 온 답은 이미 늦어서
#: 쓸 데가 없습니다. 늦게 오느니 빨리 실패하고 다음 발언을 받는 편이 낫습니다.
_TIMEOUT_SEC = float(os.environ.get("OPENAI_TIMEOUT_SEC", "45"))

#: 추론 모델(gpt-5 계열)의 생각 깊이.
#:
#: 기본값이 `medium` 인데, 논쟁점 예측처럼 프롬프트가 긴 호출은 **22초**가 걸려
#: 위 제한을 그대로 넘겼습니다(세 번 다 실패 → 예측이 통째로 빕니다).
#: `low` 로 두면 같은 프롬프트가 10초에 끝나고 답의 모양은 그대로입니다.
#:
#: 회의는 사람이 말하는 속도로 흘러갑니다. 더 깊이 생각한 답이 20초 늦게 오면
#: 그 사이 대화가 넘어가 쓸 데가 없습니다.
_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: 비어 있지 않으면 호출이 실패한 것입니다. 루프는 이걸 보고 멈춥니다.
    error: str = ""
    error_kind: str = ""
    total_tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.error

    def __bool__(self) -> bool:
        return self.ok


def _is_retryable(exc: Exception) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(m in blob for m in _RETRYABLE)


def _to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """
    중립 도구 스펙 → OpenAI function 형식.

    스킬은 `{name, description, parameters}` 만 압니다. 이 감싸기가 스킬 쪽으로
    새어 나가면 프로바이더를 바꿀 때 스킬을 전부 고쳐야 합니다.
    """
    if not tools:
        return None
    return [{"type": "function", "function": t} for t in tools]


def _parse_arguments(raw: str) -> dict:
    """
    모델이 만든 인자 문자열을 dict 로.

    깨진 JSON 이 실제로 옵니다. 여기서 예외를 던지면 루프 한가운데서 죽고
    그때까지 모은 근거가 날아가므로, 빈 dict 로 떨어뜨리고 스킬이
    "필수 인자가 없다"로 정상 실패하게 둡니다.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("도구 인자 파싱 실패: %.200s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


class LLMClient:
    """
    OpenAI 클라이언트.

    키가 없으면 **생성 시점에 터뜨리지 않습니다.** 서버 전체가 못 뜨는 것보다,
    대리인 호출 때 오류를 돌려주고 나머지 API 는 그대로 도는 편이 낫습니다.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-5.5")
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI          # 지연 import — 미설치 환경에서도 서버는 뜹니다
            # `max_retries=0` 인 이유 — 재시도는 이 파일이 이미 합니다
            # (`_MAX_ATTEMPTS`). SDK 것을 켜 두면 3 × 2 = 6회가 되고
            # 대기 시간도 그만큼 곱해집니다.
            self._client = OpenAI(api_key=self._key,
                                  timeout=_TIMEOUT_SEC, max_retries=0)
        return self._client

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             system: str = "") -> LLMResponse:
        if not self._key:
            return LLMResponse(error="OPENAI_API_KEY 가 설정되지 않았습니다.",
                               error_kind="config")

        payload = list(messages)
        if system:
            payload = [{"role": "system", "content": system}] + payload

        kwargs = {"model": self._model, "messages": payload}
        # `reasoning_effort` 는 추론 모델만 받습니다. gpt-4 계열에 실어 보내면
        # 400 으로 통째로 실패합니다.
        if _REASONING_EFFORT and self._model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = _REASONING_EFFORT
        oa_tools = _to_openai_tools(tools)
        if oa_tools:
            kwargs["tools"] = oa_tools

        last = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw = self._ensure().chat.completions.create(**kwargs)
            except Exception as exc:                       # noqa: BLE001
                last = str(exc)
                if attempt < _MAX_ATTEMPTS and _is_retryable(exc):
                    wait = _BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning("LLM 재시도 %s/%s (%.1fs): %.200s",
                                   attempt, _MAX_ATTEMPTS, wait, last)
                    time.sleep(wait)
                    continue
                logger.error("LLM 호출 실패: %.300s", last)
                return LLMResponse(
                    error=last,
                    error_kind="retryable" if _is_retryable(exc) else "fatal")

            return self._to_response(raw)

        return LLMResponse(error=last or "알 수 없는 오류", error_kind="retryable")

    @staticmethod
    def _to_response(raw) -> LLMResponse:
        choice = raw.choices[0]
        msg = choice.message

        calls = []
        for tc in (msg.tool_calls or []):
            calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                  arguments=_parse_arguments(tc.function.arguments)))

        return LLMResponse(
            text=msg.content or "",
            tool_calls=calls,
            total_tokens=getattr(raw.usage, "total_tokens", 0) or 0,
        )

    # ── 되먹임용 메시지 조립 ───────────────────────────────
    # 루프가 프로바이더 형식을 몰라도 되도록 여기서 만들어 줍니다.

    @staticmethod
    def assistant_message(response: LLMResponse) -> dict:
        msg: dict = {"role": "assistant", "content": response.text or None}
        if response.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name,
                              "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in response.tool_calls
            ]
        return msg

    @staticmethod
    def tool_message(call: ToolCall, data: dict) -> dict:
        return {"role": "tool", "tool_call_id": call.id,
                "content": json.dumps(data, ensure_ascii=False, default=str)}


#: 앱 전역 인스턴스. 환경변수를 매번 읽지 않기 위해서입니다.
client = LLMClient()

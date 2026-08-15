"""
대리인이 실행하는 단위 = 스킬.

LLM 이 이름으로 부르고 결과를 되받습니다. 스킬은 **자기 일만 하고 판단하지 않습니다** —
답할지 유보할지는 `services/judge.py` 가 정합니다. 스킬 안에서 "근거가 약하니 그만두자"
같은 결정을 내리면, 같은 입력에 다른 결과가 나오고 왜 그랬는지 설명할 수 없게 됩니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SkillKind:
    """
    읽기와 쓰기를 가르는 이유.

    회의 중 대리인이 **읽기는 자유롭게, 쓰기는 흔적을 남기고** 해야 합니다.
    쓰기 스킬은 실행 전에 POLICY 를 다시 보고, 실행 후 `AgentRun` 에 연결됩니다.
    """
    READ = "READ"
    WRITE = "WRITE"


@dataclass
class SkillResult:
    """
    스킬 실행 결과.

    `ok=False` 는 **스킬이 실패한 것**이지 대리인이 답을 못 한다는 뜻이 아닙니다.
    검색이 0건이어도 `ok=True` 에 빈 `evidence` 입니다 — "찾았는데 없었다" 와
    "검색 자체가 터졌다" 를 판정 단계에서 구별해야 하기 때문입니다.
    """
    ok: bool
    message: str
    data: dict = field(default_factory=dict)
    error_code: str = ""

    #: 이 스킬이 모은 근거. `AgentRun.evidence` 로 누적됩니다.
    evidence: list[dict] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str, error_code: str = "internal") -> "SkillResult":
        return cls(ok=False, message=message, error_code=error_code)


@dataclass
class SkillContext:
    """
    스킬 한 번의 실행 환경.

    `principal` 과 `actor` 를 나눠 두는 이유 — 대리인은 **남의 요청을 받아 본인을
    대리**합니다. 검색 결과가 principal 의 기록인지 판정할 때 이 구분이 필요하고,
    그것이 유보 규칙 R2(`NOT_MY_RECORD`) 의 입력이 됩니다.
    """
    #: 대리 대상. "누구를 대신해 말하는가"
    principal_id: str
    #: 요청자. 회의에서 질문을 던진 사람. 웹 대화면 principal 과 같습니다.
    actor_id: str | None = None

    meeting_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None

    #: POLICY 스냅샷. 실행 중 설정이 바뀌어도 한 번의 실행은 같은 정책으로 끝납니다.
    settings_snapshot: dict = field(default_factory=dict)

    def is_principal(self, user_id) -> bool:
        return str(user_id) == str(self.principal_id)


class SkillBase:
    """
    모든 스킬의 부모.

    `parameters` 는 JSON Schema 입니다. 프로바이더별 도구 형식 변환은
    `services/llm.py` 가 맡고, 스킬은 형식을 모릅니다 — 모델을 바꿀 때
    스킬 20개를 다시 쓰는 일이 없도록.
    """
    name: str = ""
    description: str = ""
    kind: str = SkillKind.READ
    parameters: dict = {}

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        raise NotImplementedError

    def to_tool_spec(self) -> dict:
        """프로바이더 중립 형식. 변환은 llm.py 가 합니다."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

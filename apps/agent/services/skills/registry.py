"""
스킬 등록과 호출.

레지스트리를 두는 이유는 **LLM 이 이름 문자열로 부르기 때문**입니다. 호출 지점이
흩어지면 없는 이름이 왔을 때 어디서 터지는지 추적이 안 됩니다.
"""
from __future__ import annotations

import logging

from .base import SkillBase, SkillContext, SkillKind, SkillResult

logger = logging.getLogger("bordo.agent")


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, SkillBase] = {}

    def register(self, skill: SkillBase) -> None:
        if not skill.name:
            raise ValueError("스킬에 name 이 없습니다.")
        if skill.name in self._skills:
            # 같은 이름을 두 번 등록하면 나중 것이 조용히 이깁니다. 그 상태로
            # 배포되면 "분명 고쳤는데 안 바뀐다" 가 되므로 등록 시점에 막습니다.
            raise ValueError(f"스킬 이름이 중복됩니다: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillBase | None:
        return self._skills.get(name)

    def list(self, kind: str | None = None) -> list[SkillBase]:
        rows = list(self._skills.values())
        if kind:
            rows = [s for s in rows if s.kind == kind]
        return rows

    def build_catalog(self, kind: str | None = None) -> list[dict]:
        """LLM 에게 넘길 도구 목록."""
        return [s.to_tool_spec() for s in self.list(kind)]

    def dispatch(self, name: str, args: dict, ctx: SkillContext) -> SkillResult:
        """
        스킬 하나를 실행합니다.

        **예외를 밖으로 던지지 않습니다.** ReAct 루프 한가운데서 터지면 그때까지
        모은 `steps` 와 `evidence` 가 통째로 날아가, 왜 그렇게 답했는지 추적할 수
        없게 됩니다. 실패도 결과의 한 종류로 되돌려 루프가 판단하게 합니다.
        """
        skill = self.get(name)
        if skill is None:
            # LLM 이 없는 이름을 지어내는 일은 실제로 일어납니다.
            logger.warning("없는 스킬 호출: %s", name)
            return SkillResult.fail(f"'{name}' 스킬이 없습니다.", "not_found")

        try:
            result = skill.run(args, ctx)
        except Exception:                             # noqa: BLE001
            # 예외 문구를 `message` 에 담지 않습니다.
            #
            # `message` 는 규약상 **사용자에게 그대로 보여줄 한국어**이고, 실패
            # 사유는 이제 모델에게도 전달됩니다. `str(exc)` 를 넣으면 Django 의
            # 영문 오류나 제약 조건 이름·질의값이 그대로 실려 나가고, 모델이
            # 그것을 답변에 옮겨 적을 수 있습니다.
            #
            # 대리인은 **남의 요청을 받아 본인을 대리**합니다. 요청자는 본인이
            # 아닐 수 있으므로, 새어 나가는 것이 남에게 보이는 것과 같습니다.
            #
            # 진짜 내용은 위의 `logger.exception` 이 스택까지 남깁니다.
            logger.exception("스킬 실행 실패: %s", name)
            return SkillResult.fail(f"'{name}' 을 실행하지 못했습니다. "
                                    "다른 방법을 찾거나 확인이 필요하다고 답하십시오.",
                                    "internal")

        if not isinstance(result, SkillResult):
            # 스킬이 dict 나 None 을 돌려주면 루프가 조용히 오작동합니다.
            logger.error("스킬이 SkillResult 를 반환하지 않음: %s", name)
            return SkillResult.fail(f"'{name}' 의 반환 형식이 잘못됐습니다.", "internal")

        return result


#: 앱 전역 레지스트리. 스킬 모듈이 import 시점에 자신을 등록합니다.
registry = SkillRegistry()

__all__ = ["SkillRegistry", "registry", "SkillBase", "SkillContext",
           "SkillResult", "SkillKind"]

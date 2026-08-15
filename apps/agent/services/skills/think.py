"""
사고 정리 스킬.

데이터를 바꾸지 않습니다. **`AgentRun.steps` 에 사고 과정을 남기는 것**이 목적입니다.

이게 있어야 "AI 가 어떤 단계를 밟았는지" 화면이 그려집니다. 대리인이 무엇을 보고
어떻게 판단했는지 사람이 되짚을 수 없으면, 발언을 신뢰할 근거도 없습니다.
"""
from __future__ import annotations

from .base import SkillBase, SkillContext, SkillKind, SkillResult


class ThinkSkill(SkillBase):
    name = "think"
    kind = SkillKind.READ
    description = (
        "여러 단계를 밟아야 할 때 먼저 계획을 세웁니다. "
        "데이터를 바꾸지 않으며, 무엇을 어떤 순서로 확인할지 정리할 때 씁니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "무엇을 확인할지와 그 이유",
            }
        },
        "required": ["reasoning"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        reasoning = (args.get("reasoning") or "").strip()
        if not reasoning:
            # 빈 사고를 steps 에 남기면 화면에 빈 줄이 생깁니다.
            return SkillResult.fail("reasoning 은 필수입니다.", "validation")
        return SkillResult(ok=True, message="정리했습니다.",
                           data={"reasoning": reasoning})

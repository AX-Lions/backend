"""
다른 사람의 대리인에게 물어보는 스킬.

내 기록만으로 답이 안 될 때 씁니다. 예를 들어 회의 화면 개발 상황을 묻는데
모바일 대응은 다른 사람이 하고 있다면, 그 사람의 대리인에게 확인합니다.

## 왜 스킬로 두는가

유보(`defer`)는 스킬로 두지 않았습니다. 모델이 스스로 "답 못 하겠다" 를 고르게
하면 판정을 코드로 뺀 의미가 사라지기 때문입니다.

조회는 다릅니다. **무엇을 더 알아봐야 하는지는 모델이 판단할 일**입니다.
누구에게 물을지, 무엇을 물을지가 질문마다 다릅니다. 다만 **깊이 제한과 기록은
코드가** 합니다 — 모델에게 맡기면 서로 묻는 것이 끝나지 않습니다.
"""
from __future__ import annotations

import logging

from .base import SkillBase, SkillContext, SkillKind, SkillResult

logger = logging.getLogger("bordo.agent")


class AskPeerAgentSkill(SkillBase):
    """
    다른 팀원의 대리인에게 묻습니다.

    결과는 `AgentLookup` 으로 남고 작업 플로우에 `AI 조회` 화살표가 그려집니다.
    답을 못 받아도 기록은 남습니다 — "물어봤는데 답을 못 받았다" 도 사용자가
    알아야 할 사실입니다.
    """
    name = "ask_peer_agent"
    kind = SkillKind.WRITE
    description = (
        "내 기록만으로 답할 수 없을 때, 그 일을 맡은 팀원의 대리인에게 확인합니다. "
        "확인된 내용을 돌려주며 조회 기록이 남습니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target_name": {"type": "string",
                            "description": "물어볼 팀원의 이름"},
            "topic": {"type": "string",
                      "description": "무엇에 대한 확인인지 한 줄. 예: 모바일 반응형 작업 진행 현황"},
            "reason": {"type": "string",
                       "description": "왜 이 확인이 필요한지"},
            "question": {"type": "string", "description": "실제로 물을 문장"},
        },
        "required": ["target_name", "question"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        from apps.accounts.models import User
        from apps.orgs.models import ProjectMember

        from ..lookup import ask_peer

        question = (args.get("question") or "").strip()
        target_name = (args.get("target_name") or "").strip()
        if not question or not target_name:
            return SkillResult.fail("target_name 과 question 은 필수입니다.", "validation")
        if not ctx.project_id:
            return SkillResult.fail("프로젝트 범위가 없습니다.", "validation")

        asker = User.objects.filter(pk=ctx.principal_id).first()
        if asker is None:
            return SkillResult.fail("대리 대상을 찾을 수 없습니다.", "not_found")

        # 같은 프로젝트 참여자에게만 묻습니다. 이름만으로 아무나 찾으면
        # 남의 팀 기록을 끌어올 수 있습니다.
        target = (User.objects
                  .filter(id__in=ProjectMember.objects
                          .filter(project_id=ctx.project_id).values("user_id"),
                          name=target_name)
                  .exclude(pk=asker.pk)
                  .first())
        if target is None:
            return SkillResult.fail(
                f"{target_name} 님을 이 프로젝트에서 찾을 수 없습니다.", "not_found")

        run = None
        if ctx.run_id:
            from ..models import AgentRun
            run = AgentRun.objects.filter(pk=ctx.run_id).first()

        lookup = ask_peer(
            asker=asker, target=target,
            topic=args.get("topic") or question,
            reason=args.get("reason") or "",
            question=question,
            project_id=ctx.project_id,
            run=run,
        )
        if lookup is None:
            # 깊이 초과이거나 상대 실행이 실패했습니다. 둘 다 정상 실패라
            # 루프를 죽이지 않고 모델에게 되돌려 줍니다.
            return SkillResult.fail(
                "지금은 다른 대리인에게 확인할 수 없습니다.", "unavailable")

        if not lookup.answer:
            return SkillResult(
                ok=True,
                message=f"{target.name} 님의 Bordo 도 답을 유보했습니다.",
                data={"lookup_id": str(lookup.id), "answered": False})

        return SkillResult(
            ok=True,
            message=lookup.answer,
            data={"lookup_id": str(lookup.id), "answered": True,
                  "target": target.name})

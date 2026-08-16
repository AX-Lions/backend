"""
유보를 사용자에게 남기기.

판정은 `judge.py` 가 끝냈습니다. 여기서는 그 결과를 **본인이 돌아왔을 때 볼 수 있는
형태**로 바꿉니다. 남기지 않으면 대리인이 침묵한 사실 자체가 사라집니다.

## `defer` 스킬을 만들지 않은 이유

설계 초안에는 `defer` 를 스킬 목록에 넣었습니다. 구현하면서 뺐습니다.

스킬은 **LLM 이 부르는 것**입니다. 유보를 스킬로 두면 모델이 스스로 유보 여부를
정하게 되고, 판정을 코드로 뺀 의미가 사라집니다. 반대로 모델이 유보를 **부르지
않기로** 결정할 수도 있습니다.

유보는 판정 결과이지 모델의 선택지가 아닙니다. 그래서 루프가 직접 남깁니다.
"""
from __future__ import annotations

import logging

from django.db import transaction

from ..models import PendingQuestion

logger = logging.getLogger("bordo.agent")

#: 제목 길이. 모델 필드가 200자입니다.
_TITLE_MAX = 200
#: 목록에서 한눈에 들어오는 길이. 넘으면 잘라 씁니다.
_TITLE_SOFT = 60


def _title_of(question: str) -> str:
    """
    질문에서 목록에 뜰 한 줄을 만듭니다.

    LLM 을 부르지 않습니다. 요약을 맡기면 호출이 한 번 더 늘고, 무엇보다
    **질문이 다르게 적히면** 본인이 "내가 이런 질문을 받았나" 하고 헷갈립니다.
    원문을 그대로 자르는 편이 정확합니다.
    """
    text = " ".join((question or "").split())
    if not text:
        return "확인이 필요한 질문"
    if len(text) <= _TITLE_SOFT:
        return text[:_TITLE_MAX]
    return text[:_TITLE_SOFT].rstrip() + "…"


def _body_of(question: str, reason_message: str, evidence: list[dict]) -> str:
    """
    본문에는 **무엇을 물었고 왜 답하지 않았는지**를 함께 적습니다.

    사유만 있으면 무슨 질문이었는지 되짚을 수 없고, 질문만 있으면 대리인이
    왜 가만히 있었는지 알 수 없습니다.
    """
    parts = [f"질문: {(question or '').strip()}", "", f"보류 사유: {reason_message}"]

    names = [e.get("title_snapshot", "") for e in (evidence or [])[:5]
             if e.get("title_snapshot")]
    if names:
        parts += ["", "참고한 기록:"] + [f"- {n}" for n in names]
    return "\n".join(parts)


@transaction.atomic
def record(*, run, question: str, reason_message: str,
           evidence: list[dict] | None = None, asker=None) -> PendingQuestion | None:
    """
    유보를 `PendingQuestion` 으로 남깁니다.

    회의가 없는 실행(웹 대화)에서는 만들지 않습니다 — 모델이 회의를 필수로 요구하고,
    무엇보다 **본인과의 대화에서 나온 유보는 되물을 상대가 자기 자신**이라
    목록에 쌓아 둘 이유가 없습니다.
    """
    if run.meeting_id is None:
        return None

    # 같은 실행이 두 번 남기지 않습니다. 재시도나 중복 호출에서 목록이 불어나면
    # 사용자는 같은 질문을 여러 번 답해야 합니다.
    #
    # 손으로 "있는지 보고 없으면 넣는" 방식은 동시에 불리면 뚫립니다. 둘 다
    # "없다" 를 보고 둘 다 넣습니다. `get_or_create()` 는 **세이브포인트 안에서
    # 만들고 IntegrityError 가 나면 다시 읽는** 동작이 이미 들어 있어, 모델의
    # 유니크 제약(`uq_pending_question_run`)과 짝을 이루면 그 경합이 닫힙니다.
    #
    # 직접 짜지 않는 이유가 이것입니다 — 같은 패턴을 곳곳에 복붙하면 나중에
    # 한 곳만 고쳐 놓고 다 고친 줄 압니다.
    # `chat_room_id` 만 콜러블입니다.
    #
    # 파이썬은 `get_or_create()` 를 부르기 **전에** 인자를 다 계산합니다. 그냥
    # 넣으면 이미 행이 있어 `get` 으로 끝나는 경우에도 방을 만들고 `RoomMember`
    # 까지 넣은 뒤 그 결과를 버립니다. Django 는 `defaults` 의 콜러블을
    # **만들 때만** 풀어 주므로(`resolve_callables`), 부수효과가 있는 값은
    # 이렇게 미뤄야 합니다.
    question_obj, created = PendingQuestion.objects.get_or_create(
        run=run,
        defaults=dict(
            meeting_id=run.meeting_id,
            asker=asker,
            asker_name=getattr(asker, "name", "") or "",
            target_user=run.user,
            title=_title_of(question),
            body=_body_of(question, reason_message, evidence or []),
            chat_room_id=lambda: _answer_room_id(run.user, asker),
        ),
    )

    # 앞선 시도에서 방을 못 잡았으면 이번에 채웁니다.
    #
    # 방 준비는 실패해도 유보를 남기도록 돼 있어 `chat_room_id` 가 빈 채로
    # 저장될 수 있습니다. 재시도에서 그냥 있는 행을 돌려주기만 하면 **답변 창이
    # 영영 안 열립니다** — 채팅 쪽이 잠깐 흔들린 대가로 그 유보만 평생 못 답합니다.
    if not created and question_obj.chat_room_id is None:
        room_id = _answer_room_id(run.user, asker)
        if room_id is not None:
            question_obj.chat_room_id = room_id
            question_obj.save(update_fields=["chat_room_id", "updated_at"])

    return question_obj


def _answer_room_id(owner, asker):
    """
    답변을 쓸 방을 미리 잡아 둡니다.

    화면에서 유보 질문을 누르면 **그 자리에서 답변 창이 열립니다.** 방 id 가 없으면
    클릭한 뒤에야 방을 만들게 되고, 그 사이 왕복 한 번이 더 생깁니다. 무엇보다
    만들다 실패하면 사용자는 "답변하기가 안 눌린다"만 보게 됩니다.

    질문한 사람이 있으면 그 사람과의 `PEER_AGENT` 방입니다 — 남이 내 대리인에게
    물은 것이라 대화의 상대가 정해져 있습니다. 질문자가 없으면(시스템 유보) 본인의
    AI 방으로 보냅니다.

    **방을 못 잡아도 `None` 을 돌려주고 끝냅니다.** 유보 기록 자체는 남아야 합니다 —
    답변 창이 한 번 덜 열리는 것과 "그 질문이 있었다"가 통째로 사라지는 것은
    비교가 안 됩니다.
    """
    from apps.chat.models import ChatRoom, RoomMember, RoomType
    from apps.chat.services import ensure_ai_room, peer_agent_key
    from apps.common.db import ensure_row

    try:
        if asker is None or asker.id == owner.id:
            return ensure_ai_room(owner).id

        # `get_or_create()` 를 못 쓰는 자리입니다. 소프트 삭제된 방이 유니크
        # 제약을 잡고 있으면 기본 매니저에는 안 보여서, 만들면 IntegrityError 가
        # 나고 다시 읽어도 여전히 안 보입니다. 사유는 `common/db.py` 에 적었습니다.
        room, _ = ensure_row(
            ChatRoom,
            type=RoomType.PEER_AGENT, dedupe_key=peer_agent_key(asker.id, owner.id),
            defaults=dict(agent_owner=owner, created_by=owner),
        )

        # 양쪽을 다 넣습니다. 질문한 사람만 넣으면 정작 답할 사람의 목록에 방이
        # 안 뜹니다.
        for u in (asker, owner):
            RoomMember.objects.get_or_create(room=room, user=u)
        return room.id
    except Exception:                                          # noqa: BLE001
        # 이 except 가 넓은 것은 의도입니다.
        #
        # 여기서 잡는 것은 "방 만들다 난 경합" 만이 아니라 **채팅 쪽에서 나는
        # 모든 실패**입니다. 무엇이 터지든 유보 기록보다 중요하지 않습니다 —
        # 답변 창이 한 번 덜 열리는 것과 "그 질문이 있었다" 가 통째로 사라지는
        # 것은 비교가 안 됩니다.
        #
        # 대신 조용히 넘어가지 않게 run 까지 실어 ERROR 로 남깁니다. 좁혀 두면
        # 예상 못 한 예외가 유보를 데리고 사라집니다.
        logger.exception("유보 답변 방 준비 실패 owner=%s asker=%s",
                         getattr(owner, "id", None), getattr(asker, "id", None))
        return None

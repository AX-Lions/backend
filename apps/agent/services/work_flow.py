"""
작업 플로우 화살표 만들기.

회의 플로우는 대리인이 그립니다(`flow.py`). 작업 플로우는 **사람이 일한 흔적**을
그리므로 만드는 주체가 다릅니다.

    작업     WorkItem 생성 · 상태 변경        apps.states
    수정     문서 수정                        apps.documents
    공유     문서 전달                        apps.documents
    피드백   채팅에 남긴 의견                 apps.chat
    AI 조회  대리인이 다른 대리인에게 질의     apps.agent  (lookup.py)

## 왜 시그널로 관찰하는가

발생 지점이 전부 **다른 사람 앱**입니다. `CLAUDE.md` 는 남의 앱 `views.py` 를
고치지 말라고 합니다. `publish()` 로 흘리는 방법을 먼저 봤는데 안 됩니다 —
WebSocket 단방향 브로드캐스트라 구독 지점이 없고, `apps.states` 는 이벤트를
하나도 발행하지 않습니다.

`post_save` 로 **모델만 봅니다.** 남의 코드를 한 줄도 건드리지 않습니다.

## 무엇을 남기지 않는가

회의 플로우와 같은 기준입니다 — **"의미 있는 정보 변화" 만.**

    남긴다    작업 생성 · 상태 변경 · 문서 생성/수정 · 전달 · 피드백
    안 남긴다  진행률 1% 변경 · 오타 수정 · 조회

전부 남기면 화살표에 묻혀 정작 무슨 일이 있었는지가 안 보입니다.

## 저장을 방해하지 않습니다

시그널이 터져도 원래 저장은 성공해야 합니다. 화살표 하나 때문에 작업 생성이
실패하면 안 됩니다. `post_save` 는 이미 커밋 경로 안이라, 실패는 삼키고 로그만
남깁니다.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("bordo.agent")

#: 라벨 길이. 모델이 60자입니다.
_LABEL_MAX = 60


def _teammates(project_id, exclude_user_id):
    """
    같은 프로젝트의 다른 참여자들.

    작업 화살표는 "누가 했다" 를 팀에게 알리는 것이라 받는 쪽이 나머지 전원입니다.
    받는 쪽이 없으면(혼자인 프로젝트) 그리지 않습니다 — 화살표는 흐름이고,
    흐를 곳이 없으면 흐름이 아닙니다.
    """
    from apps.orgs.models import ProjectMember

    return [m.user for m in (ProjectMember.objects
                             .filter(project_id=project_id)
                             .exclude(user_id=exclude_user_id)
                             .select_related("user")
                             .order_by("user_id"))]


def record(*, project_id, actor, content_type, label, targets=None,
           source="", source_url="", document=None, occurred_at=None):
    """
    작업 화살표 하나를 남깁니다.

    `flow.record()` 와 나눠 둔 이유는 스코프가 다르기 때문입니다 — 저쪽은 회의,
    이쪽은 프로젝트입니다. 합치면 두 경우를 가르는 분기가 함수 안에 생깁니다.
    """
    from apps.meetings.models import FlowCategory, FlowEdge

    from . import flow

    if actor is None or not project_id:
        return None

    to_users = targets if targets is not None else _teammates(project_id, actor.id)
    if not to_users:
        return None

    try:
        src = flow.user_node(actor)
        dsts = [flow.user_node(u) for u in to_users]
        names = ", ".join(n["name"] for n in dsts[:3])
        if len(dsts) > 3:
            names += f" 외 {len(dsts) - 3}명"

        # 세이브포인트. 이 함수는 남의 앱 트랜잭션 안에서 불립니다 —
        # 여기서 난 DB 오류를 잡기만 하면 그쪽 트랜잭션이 통째로 오염됩니다.
        with transaction.atomic():
            return FlowEdge.objects.create(
                project_id=project_id,
                meeting=None,               # 작업 플로우는 회의가 없습니다
                category=FlowCategory.WORK,
                content_type=content_type,
                source=source, source_url=source_url,
                from_node=src, to_nodes=dsts,
                participant_ids=[str(actor.id)] + [str(u.id) for u in to_users],
                label=(label or "")[:_LABEL_MAX],
                direction_label=f"{src['name']} → {names}"[:200],
                extra_participant_count=max(0, len(dsts) - 3),
                # 좌측 인덱스가 이 값으로 화살표를 묶습니다. 안 넣으면 문서를
                # 고쳐도 인덱스에 그 문서가 안 뜹니다.
                work_document=document,
                occurred_at=occurred_at or timezone.now(),
            )
    except Exception:                                          # noqa: BLE001
        logger.exception("작업 플로우 기록 실패 project=%s", project_id)
        return None


# ═══════════════════════════════════════════ 시그널

def _is_private(obj) -> bool:
    """
    비공개는 그리지 않습니다.

    플로우는 **팀 관점 화면**이라 남에게 그대로 보입니다. 제목만 스쳐도
    비공개로 둔 뜻이 사라집니다.
    """
    return str(getattr(obj, "visibility", "")).lower() == "private"


def _deleted(obj) -> bool:
    return getattr(obj, "deleted_at", None) is not None


def on_work_item_saved(sender, instance, created, **kwargs):
    """
    작업이 생기거나 상태가 바뀌었습니다.

    **진행률만 바뀐 것은 그리지 않습니다.** 1% 씩 올릴 때마다 화살표가 쌓이면
    정작 무엇이 끝났는지가 안 보입니다. 상태 전이만 의미 있는 변화로 봅니다.
    """
    from apps.meetings.models import FlowContentType

    if _is_private(instance) or _deleted(instance):
        return

    if created:
        label, ctype = instance.title, FlowContentType.WORK
    else:
        # 이전 상태를 모르면 "상태가 바뀌었는지" 를 알 수 없습니다.
        # `_prev_status` 를 심어 두는 쪽(pre_save)에서 넘겨받습니다.
        before = getattr(instance, "_prev_status", None)
        if before is None or before == instance.status:
            return
        label, ctype = instance.title, FlowContentType.REVISION

    record(project_id=instance.project_id, actor=instance.owner,
           content_type=ctype, label=label)


def remember_work_status(sender, instance, **kwargs):
    """
    저장 **전에** 이전 상태를 기억해 둡니다.

    `post_save` 에서는 이미 새 값이라 무엇이 바뀌었는지 알 수 없습니다.
    DB 를 한 번 더 읽는 비용이 있지만, 상태가 안 바뀐 저장까지 화살표로
    그리는 것보다 낫습니다.
    """
    # `instance.pk` 를 보면 안 됩니다. UUIDModel 은 객체를 만들 때 이미 id 를
    # 넣어 두므로, 새 행에서도 pk 가 있어 **저장할 때마다 헛조회가 나갑니다.**
    if instance._state.adding:
        instance._prev_status = None
        return
    try:
        instance._prev_status = (sender.objects
                                 .filter(pk=instance.pk)
                                 .values_list("status", flat=True).first())
    except Exception:                                          # noqa: BLE001
        instance._prev_status = None


def remember_document_delivery(sender, instance, **kwargs):
    """저장 전에 이미 전달된 문서였는지 기억해 둡니다."""
    if instance._state.adding:
        instance._prev_delivered = False
        return
    try:
        prev = (sender.all_objects.filter(pk=instance.pk)
                .values_list("delivery_context", flat=True).first())
        instance._prev_delivered = bool(prev)
    except Exception:                                          # noqa: BLE001
        # 모르면 이미 전달된 것으로 봅니다. 중복 SHARE 를 그리는 것보다
        # 한 번 빠뜨리는 쪽이 낫습니다.
        instance._prev_delivered = True


def on_document_saved(sender, instance, created, **kwargs):
    """
    문서가 생기거나 고쳐졌습니다.

    전달(`공유`)은 문서에 `delivery_context` 가 채워질 때입니다 — 누구에게
    보냈는지가 거기 들어 있습니다.
    """
    from apps.meetings.models import FlowContentType

    if _is_private(instance) or _deleted(instance):
        return

    # 전달은 **이번 저장에서 생겼을 때만** SHARE 입니다.
    #
    # 현재 상태만 보면, 한 번 전달한 문서는 이후 제목만 고쳐도 계속 SHARE 가
    # 나갑니다. 같은 문서를 몇 번이고 "전달했다" 고 그리면 화살표에 묻혀
    # 정작 언제 전달했는지가 안 보입니다.
    newly_shared = bool(instance.delivery_context) and not getattr(
        instance, "_prev_delivered", False)

    ctype = FlowContentType.SHARE if newly_shared else (
        FlowContentType.WORK if created else FlowContentType.REVISION)

    record(project_id=instance.project_id, actor=instance.owner,
           content_type=ctype, label=instance.title, document=instance,
           source=_source_of(instance), source_url=_url_of(instance))


def _source_of(document) -> str:
    """
    산출물이 어디에 있는지. 링크에서 알아냅니다.

    화면의 `필터링 > 출처` 가 Github · Figma · Notion 세 가지입니다.
    맞는 것이 없으면 비워 둡니다 — 억지로 채우면 필터가 거짓말을 합니다.
    """
    from apps.meetings.models import FlowSource

    url = _url_of(document).lower()
    if "github" in url:
        return FlowSource.GITHUB
    if "figma" in url:
        return FlowSource.FIGMA
    if "notion" in url:
        return FlowSource.NOTION
    return ""


def _url_of(document) -> str:
    ctx = document.delivery_context or []
    for item in ctx if isinstance(ctx, list) else []:
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return ""


def remember_message_importance(sender, instance, **kwargs):
    """
    저장 전에 이전 중요 표시를 기억해 둡니다.

    화면에서 중요 표시는 **나중에 켭니다**(`PATCH /messages/{id}/important`).
    그건 UPDATE 라 `created=False` 이므로, 만들 때만 보면 **실제 사용 경로에서
    피드백 화살표가 한 번도 안 그려집니다.**
    """
    if instance._state.adding:
        instance._prev_important = False
        return
    try:
        instance._prev_important = bool(
            sender.objects.filter(pk=instance.pk)
            .values_list("is_important", flat=True).first())
    except Exception:                                          # noqa: BLE001
        instance._prev_important = None


def on_chat_message_saved(sender, instance, created, **kwargs):
    """
    채팅에 남긴 의견을 `피드백` 으로 남깁니다.

    ## 방 종류를 반드시 봅니다

    `project_id` 만 보면 **개인 대화가 팀 전체에 샙니다.** `DIRECT` 와
    `PEER_AGENT` 방에도 `project` 가 붙습니다 — 사이드바에서 프로젝트별로
    묶어 보여주려는 용도입니다(`chat/models.py`).

    그 방의 메시지로 화살표를 그리면 `label` 에 **본문이 그대로 실려** 플로우
    화면에서 팀 전원에게 보입니다. 1:1 로 나눈 말이 팀에 공개되는 셈입니다.

    ## 중요 표시된 것만 그립니다

    모든 메시지를 그리면 플로우가 대화 로그가 됩니다 — 회의 플로우에서 검색
    호출을 안 그린 것과 같은 이유입니다.

    **켜지는 순간에만** 그립니다. 껐다 켜도 한 번, 이미 켜진 것을 다시 저장해도
    안 그립니다.

    대리인이 보낸 것은 제외합니다. 그건 회의 플로우가 이미 그립니다.
    """
    from apps.chat.models import RoomType
    from apps.meetings.models import FlowContentType

    if instance.is_agent or not instance.is_important:
        return

    room = instance.room
    if not room or not room.project_id:
        return
    if room.type != RoomType.PROJECT:
        # 개인 대화(DIRECT · PEER_AGENT · AI)는 팀 화면에 그리지 않습니다.
        return

    # 이번 저장에서 켜진 것만. 이미 켜져 있던 것을 다시 저장하면 안 그립니다.
    if not created and getattr(instance, "_prev_important", None):
        return

    record(project_id=room.project_id, actor=instance.sender,
           content_type=FlowContentType.FEEDBACK,
           label=instance.body or "피드백")

"""
이벤트 발행 지점.

A(Discord) · B(실시간) · D(API 서버) 가 공유하는 인터페이스입니다.
쓰기가 일어난 쪽은 여기만 부르고, 어디로 어떻게 나가는지는 신경 쓰지 않습니다.

    publish(project_id, "chat.message.created", {"room_id": ..., "message_id": ...})

**2단계(지금)** — 로그만 남깁니다.
**3단계** — B 가 이 함수 몸통을 Channels `group_send` 로 갈아끼웁니다.
호출부는 한 줄도 안 바뀝니다.

`transaction.on_commit` 으로 감싸는 이유:
커밋 전에 쏘면 롤백된 트랜잭션의 이벤트가 나갑니다. 클라이언트는
"생성됐다"는 이벤트를 받고 조회했는데 없는 상황을 만나게 됩니다.
"""
import logging

from django.db import transaction

logger = logging.getLogger("bordo.events")


def publish(project_id, event_type, payload=None, *, user_id=None):
    """
    프로젝트 채널로 이벤트 하나를 흘립니다.

    :param project_id: 이벤트가 흐를 채널. `/ws/projects/{project_id}` 와 같습니다.
                       프로젝트에 매이지 않는 개인 알림은 None + user_id 로 보냅니다.
    :param event_type: `chat.message.created` 처럼 점으로 구분한 이름.
    :param payload:    직렬화 가능한 dict. 모델 인스턴스를 그대로 넣지 마십시오 —
                       수신 측이 커밋 이후 상태를 다시 읽을 수 없습니다.
    :param user_id:    특정 사용자에게만 갈 이벤트일 때.
    """
    body = {
        "event_type": event_type,
        "project_id": str(project_id) if project_id else None,
        "user_id": str(user_id) if user_id else None,
        "payload": payload or {},
    }
    transaction.on_commit(lambda: _deliver(body))


def _deliver(body):
    """
    Channels 그룹으로 흘립니다.

    **호출부는 한 줄도 바뀌지 않았습니다.** `publish()` 를 부르는 쪽은 이 함수가
    로그를 찍든 소켓으로 나가든 모릅니다.

    여기서 예외를 밖으로 던지지 않습니다. 실시간 전달이 실패했다고 이미 커밋된
    쓰기 요청까지 500 으로 되돌릴 수는 없습니다 — 화면이 늦게 갱신될 뿐입니다.
    """
    logger.info("event %s project=%s user=%s", body["event_type"],
                body["project_id"], body["user_id"])
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return

        message = {"type": "bordo.event", "body": body}
        if body["project_id"]:
            async_to_sync(layer.group_send)(f"project.{body['project_id']}", message)
        if body["user_id"]:
            async_to_sync(layer.group_send)(f"user.{body['user_id']}", message)
    except Exception:                                          # noqa: BLE001
        logger.warning("실시간 전달 실패 %s", body["event_type"], exc_info=True)

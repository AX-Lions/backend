"""
남의 앱 모델을 관찰해 작업 플로우 화살표를 그립니다.

## 왜 여기서 붙이는가

작업 화살표가 생기는 지점은 `apps.states` · `apps.documents` · `apps.chat` 인데
전부 다른 사람 앱입니다. `CLAUDE.md` 는 남의 `views.py` 를 고치지 말라고 합니다.

시그널은 **모델을 관찰만** 합니다. 그쪽 코드는 한 줄도 바뀌지 않고, 이 파일만
읽으면 어떤 모델이 플로우에 영향을 주는지 한눈에 보입니다.

## 연결을 여기 모아 두는 이유

`@receiver` 데코레이터를 서비스 함수 위에 흩어 두면 "무엇이 무엇을 듣고
있는가" 가 파일 여러 개에 퍼집니다. 연결은 여기, 판단은 `work_flow.py` 로
나눠 둡니다.
"""
from django.db.models.signals import post_save, pre_save

from .services import work_flow


def connect():
    """`AppConfig.ready()` 에서 한 번 부릅니다."""
    from apps.chat.models import ChatMessage
    from apps.documents.models import Document
    from apps.states.models import WorkItem

    # dispatch_uid 를 주지 않으면 앱이 두 번 로드될 때 화살표가 두 개 생깁니다.
    pre_save.connect(work_flow.remember_work_status, sender=WorkItem,
                     dispatch_uid="bordo.work_flow.remember_work_status")
    post_save.connect(work_flow.on_work_item_saved, sender=WorkItem,
                      dispatch_uid="bordo.work_flow.work_item")
    pre_save.connect(work_flow.remember_document_delivery, sender=Document,
                     dispatch_uid="bordo.work_flow.remember_document_delivery")
    post_save.connect(work_flow.on_document_saved, sender=Document,
                      dispatch_uid="bordo.work_flow.document")
    pre_save.connect(work_flow.remember_message_importance, sender=ChatMessage,
                     dispatch_uid="bordo.work_flow.remember_message_importance")
    post_save.connect(work_flow.on_chat_message_saved, sender=ChatMessage,
                      dispatch_uid="bordo.work_flow.chat_message")

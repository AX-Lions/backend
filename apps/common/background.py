"""
개발 환경에서 **요청을 막지 않고** 뒤로 미루기.

## 왜 필요한가

`CELERY_TASK_ALWAYS_EAGER=1` 은 Redis 없이 돌리려고 켜 둔 것인데, 이 설정에서
`task.delay()` 는 **그 자리에서 동기로 실행**됩니다. 그래서 불참 등록 한 번이
논쟁점 예측(LLM)이 끝날 때까지 붙잡혀 응답이 60초를 넘겼습니다 — 사용자는
버튼이 안 눌린 줄 알고 다시 누릅니다.

작업 자체는 이미 "기다리지 않는다"를 전제로 짜여 있습니다(결과는 DB 에 남고
화면이 나중에 읽습니다). 막고 있던 것은 실행 방식뿐이라 여기서 스레드로 옮깁니다.

## 운영에서는 아무것도 안 바뀝니다

`CELERY_TASK_ALWAYS_EAGER` 가 꺼져 있으면 평소대로 `delay()` 로 큐에 넣습니다.
이 파일은 **개발·시연 환경의 EAGER 만** 다룹니다.

## 커밋 뒤에 시작합니다

`transaction.on_commit()` 으로 감쌉니다. 트랜잭션 안에서 스레드를 띄우면 아직
커밋되지 않은 행을 다른 연결에서 읽게 되어, 방금 만든 참석자·회의를 **못 찾고**
조용히 아무 일도 안 한 것처럼 끝납니다.
"""
from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger("bordo.background")


def run_soon(task, *args, **kwargs) -> None:
    """Celery 작업을 요청 밖에서 돌립니다. 실패는 로그로만 남깁니다."""
    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        transaction.on_commit(lambda: task.delay(*args, **kwargs))
        return

    def _run():
        try:
            # 작업 함수를 그대로 부릅니다. `delay()` 를 쓰면 EAGER 설정 때문에
            # 다시 동기로 돌 뿐이고, 스레드로 옮긴 뜻이 사라집니다.
            task(*args, **kwargs)
        except Exception:                                      # noqa: BLE001
            # 여기서 터져도 요청은 이미 끝났습니다. 삼키지 않고 남깁니다 —
            # 조용히 사라지면 "대리인이 아무 말도 안 한다" 로만 보입니다.
            logger.exception("백그라운드 작업 실패: %s", getattr(task, "name", task))
        finally:
            # 스레드마다 연결이 따로 열립니다. 안 닫으면 SQLite 파일 잠금이
            # 남아 다음 쓰기가 `database is locked` 로 실패합니다.
            connection.close()

    transaction.on_commit(
        lambda: threading.Thread(target=_run, daemon=True).start())

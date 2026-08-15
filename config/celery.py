"""
Celery 앱.

대리인 실행은 회의 흐름을 막으면 안 되므로 비동기로 돌립니다.

## 개발 중에는 Redis 가 없어도 됩니다

`CELERY_TASK_ALWAYS_EAGER=1` 이면 큐를 거치지 않고 그 자리에서 실행합니다.
브로커를 띄우지 않고도 전체 흐름을 확인할 수 있습니다.

**운영에서 이 값을 켜 두면 안 됩니다.** 요청 스레드에서 LLM 호출이 통째로
돌아 응답이 수십 초씩 걸립니다.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("bordo")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

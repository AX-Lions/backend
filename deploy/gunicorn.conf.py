"""
gunicorn 설정.

    gunicorn -c deploy/gunicorn.conf.py config.wsgi:application

값을 코드에 박지 않고 환경변수에서 읽습니다. 임시 서버(라즈베리파이 5)에서
대회 측 서버로 옮길 때 .env 만 바꾸면 되도록 하기 위해서입니다.
"""
import multiprocessing
import os

# 8000 은 이 서버에서 다른 프로젝트 컨테이너가 이미 쓰고 있습니다.
# 외부 노출은 Cloudflare Tunnel 이 담당하므로 루프백에만 엽니다.
bind = os.environ.get("BORDO_BIND", "127.0.0.1:8010")

# 코어당 2+1 이 일반적인 출발점이지만, 라즈베리파이는 메모리가 코어보다 먼저 막힙니다.
# 워커 하나가 Django 를 통째로 올려 200MB 안팎을 씁니다.
_default_workers = min(multiprocessing.cpu_count() + 1, 5)
workers = int(os.environ.get("BORDO_WORKERS", _default_workers))

# ASGI 워커입니다. WSGI(sync) 로는 WebSocket 이 아예 안 붙습니다.
#
# gunicorn 을 유지하고 워커만 바꾸는 이유 — 프로세스 관리·재시작·상한 설정이
# 이미 여기 있습니다. uvicorn 을 직접 띄우면 그걸 다시 만들어야 합니다.
worker_class = "uvicorn.workers.UvicornWorker"

# ASGI 워커에서 이 값은 **요청 시간이 아니라 워커 무응답 감지** 기준입니다.
# WebSocket 이 오래 열려 있어도 워커는 계속 신호를 보내므로 끊기지 않습니다.
timeout = int(os.environ.get("BORDO_TIMEOUT", "120"))

# keep-alive 를 터널의 idle 보다 짧게 둡니다. 반대면 이미 닫힌 연결로 응답하려다
# 502 가 간헐적으로 납니다.
keepalive = 15

# 워커 재활용은 꺼 둡니다.
#
# WSGI 시절에는 누수 방지에 유용했지만, 지금은 워커가 WebSocket 을 오래 물고
# 있습니다. 재활용이 돌면 **연결된 사용자가 통째로 끊깁니다** — 회의 중에
# 화면이 멈추는 쪽이 메모리보다 큰 문제입니다.
#
# 켜야 하면 환경변수로 올리되, 끊김을 감수한다는 뜻입니다.
max_requests = int(os.environ.get("BORDO_MAX_REQUESTS", "0"))
max_requests_jitter = 100

# 그레이스풀 재시작 시 처리 중이던 요청을 끝낼 시간.
graceful_timeout = 30

# systemd 가 journald 로 받습니다. 파일로 쓰면 회전 관리를 따로 해야 합니다.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("BORDO_LOG_LEVEL", "info")

# 터널이 넘겨주는 원래 클라이언트 IP 를 로그에 남깁니다.
access_log_format = '%({x-forwarded-for}i)s %(m)s %(U)s %(s)s %(L)ss'

# 프록시가 붙인 헤더를 신뢰할 대상. 터널이 같은 호스트에서 돌므로 루프백만 신뢰합니다.
forwarded_allow_ips = os.environ.get("BORDO_FORWARDED_ALLOW_IPS", "127.0.0.1")

proc_name = "bordo-backend"

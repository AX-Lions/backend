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

# 동기 워커입니다. WebSocket 이 붙으면 그때 Channels + uvicorn 으로 갑니다 —
# 지금 비동기 워커로 두면 DB 커넥션 재사용과 맞물려 문제만 늘어납니다.
worker_class = "sync"

# ReAct 검색과 LLM 호출은 느립니다. 기본 30초로는 정상 요청도 끊깁니다.
timeout = int(os.environ.get("BORDO_TIMEOUT", "120"))

# keep-alive 를 터널의 idle 보다 짧게 둡니다. 반대면 이미 닫힌 연결로 응답하려다
# 502 가 간헐적으로 납니다.
keepalive = 15

# 워커를 주기적으로 재활용해 누수가 쌓이지 않게 합니다. jitter 는 모든 워커가
# 동시에 재시작해 순간적으로 응답이 비는 것을 막습니다.
max_requests = 1000
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

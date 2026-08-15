#!/usr/bin/env bash
#
# Bordo 백엔드 배포.
#
#   ./deploy/deploy.sh
#
# GitHub Actions 러너와 사람이 같은 스크립트를 씁니다. 배포 절차가 두 벌이면
# "내 손으로는 되는데 CI 에서는 안 된다"가 생깁니다.
#
# 실패하면 즉시 멈추고 이전 버전이 그대로 돕니다. 중간까지 반영된 상태로 두지 않습니다.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

log() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

[ -f .env ] || { echo "중단: .env 가 없습니다. .env.example 을 복사해 채우십시오."; exit 1; }
set -a; . ./.env; set +a

# ── 1. 의존성 ────────────────────────────────────────────────
log "의존성"
# venv 안의 실행 스크립트는 shebang 에 절대 경로를 박아 둡니다. 프로젝트 폴더를
# 옮기면 그 경로가 사라져 systemd 가 203/EXEC 로 죽습니다 — 파일은 있는데
# 인터프리터가 없어서라, 로그만 보면 원인이 헷갈립니다.
# 대회 측 서버로 옮길 때 또 겪을 일이므로 여기서 감지해 다시 만듭니다.
if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo "  venv 가 현재 경로와 어긋납니다 — 다시 만듭니다"
  rm -rf .venv
elif [ -x .venv/bin/gunicorn ] && ! .venv/bin/gunicorn --version >/dev/null 2>&1; then
  echo "  venv 스크립트의 경로가 어긋납니다 — 다시 만듭니다"
  rm -rf .venv
fi
[ -d .venv ] || python3 -m venv .venv
# requirements.txt 가 바뀌지 않았으면 건너뜁니다. 라즈베리파이에서 psycopg 빌드가
# 매 배포마다 돌면 몇 분씩 걸립니다.
HASH=$(sha256sum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.req-hash 2>/dev/null || true)" != "$HASH" ]; then
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "$HASH" > .venv/.req-hash
  echo "  설치 완료"
else
  echo "  변경 없음 — 건너뜀"
fi

# ── 2. DB ────────────────────────────────────────────────────
log "데이터 계층 (PostgreSQL · Redis)"
docker compose -f deploy/docker-compose.db.yml --env-file .env up -d

# 마이그레이션을 DB 준비 전에 돌리면 실패합니다. healthcheck 를 기다립니다.
for i in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' bordo-db 2>/dev/null || echo none)" = "healthy" ] && break
  [ "$i" = "30" ] && { echo "중단: DB 가 90초 안에 준비되지 않았습니다."; exit 1; }
  sleep 3
done
echo "  PostgreSQL healthy"

# Redis 가 없으면 실시간이 인메모리로 떨어집니다. 오류는 안 나고 이벤트만
# 사람마다 다르게 보이므로, 여기서 확인해 두지 않으면 나중에 못 찾습니다.
for i in $(seq 1 20); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' bordo-redis 2>/dev/null || echo none)" = "healthy" ] && break
  [ "$i" = "20" ] && { echo "중단: Redis 가 준비되지 않았습니다."; exit 1; }
  sleep 3
done
echo "  Redis healthy"

# ── 3. 마이그레이션 ──────────────────────────────────────────
log "마이그레이션"
.venv/bin/python manage.py migrate --noinput

# ── 4. 정적 파일 ─────────────────────────────────────────────
# admin 화면이 CSS 없이 뜨는 것을 막습니다.
log "정적 파일"
.venv/bin/python manage.py collectstatic --noinput --clear >/dev/null
echo "  완료"

# ── 5. 점검 ──────────────────────────────────────────────────
log "설정 점검"
.venv/bin/python manage.py check --deploy 2>&1 | tail -20 || true

# ── 6. 재시작 ────────────────────────────────────────────────
log "서비스 재시작"
if systemctl is-enabled --quiet bordo-backend 2>/dev/null; then
  sudo systemctl reload-or-restart bordo-backend
  # 워커도 함께 갈아야 합니다. 웹만 재시작하면 옛 코드의 대리인이 계속 돕니다.
  if systemctl is-enabled --quiet bordo-worker 2>/dev/null; then
    sudo systemctl restart bordo-worker
    echo "  워커 재시작"
  fi
  sleep 3
  systemctl is-active --quiet bordo-backend \
    && echo "  실행 중" \
    || { echo "실패 — 최근 로그:"; journalctl -u bordo-backend -n 30 --no-pager; exit 1; }
else
  echo "  유닛이 아직 등록되지 않았습니다. 아래를 한 번 실행하십시오."
  echo "    sudo cp $ROOT/deploy/bordo-backend.service /etc/systemd/system/"
  echo "    sudo cp $ROOT/deploy/bordo-worker.service /etc/systemd/system/"
  echo "    sudo systemctl daemon-reload"
  echo "    sudo systemctl enable --now bordo-backend bordo-worker"
  exit 0
fi

# ── 7. 실제로 응답하는지 ─────────────────────────────────────
log "응답 확인"
BIND="${BORDO_BIND:-127.0.0.1:8010}"
for i in $(seq 1 10); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://${BIND}/api/v1/home" || true)
  # 인증이 없으므로 401 이 정상입니다. 200 을 기대하면 오히려 잘못된 것입니다.
  case "$CODE" in
    401|403|200) echo "  응답 $CODE — 정상"; exit 0 ;;
  esac
  sleep 2
done
echo "중단: 서비스가 응답하지 않습니다 (마지막 응답 $CODE)"
journalctl -u bordo-backend -n 30 --no-pager
exit 1

#!/usr/bin/env bash
#
# Bordo Discord 봇 배포.
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

# 봇이 뜨자마자 죽는 가장 흔한 이유입니다. 여기서 걸러야 systemd 재시작 5회를
# 다 쓰고 나서야 원인을 찾는 일이 없습니다.
for key in DISCORD_TOKEN BORDO_SERVICE_TOKEN; do
  if ! grep -qE "^${key}=.+" .env; then
    echo "중단: .env 의 ${key} 가 비어 있습니다."; exit 1
  fi
done

# ── 1. 의존성 ────────────────────────────────────────────────
log "의존성"
# venv 안의 실행 스크립트는 shebang 에 절대 경로를 박아 둡니다. 프로젝트 폴더를
# 옮기면 그 경로가 사라져 systemd 가 203/EXEC 로 죽습니다 — 파일은 있는데
# 인터프리터가 없어서라, 로그만 보면 원인이 헷갈립니다.
# 백엔드에서 실제로 겪었고, 대회 측 서버로 옮길 때 또 겪을 일입니다.
if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo "  venv 가 현재 경로와 어긋납니다 — 다시 만듭니다"
  rm -rf .venv
fi
[ -d .venv ] || python3 -m venv .venv

# requirements.txt 가 바뀌지 않았으면 건너뜁니다. 라즈베리파이에서 매 배포마다
# 의존성을 다시 받으면 배포가 몇 분씩 늘어집니다.
HASH=$(sha256sum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.req-hash 2>/dev/null || true)" != "$HASH" ]; then
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "$HASH" > .venv/.req-hash
  echo "  설치 완료"
else
  echo "  변경 없음 — 건너뜀"
fi

# ── 2. 임포트 점검 ───────────────────────────────────────────
# 봇을 실제로 띄우기 전에 코드가 임포트되는지만 봅니다. 오타 하나로 죽는 것을
# 여기서 잡으면, 게이트웨이에 붙었다 끊었다 하며 재시작을 반복하지 않습니다.
#
# main.py 를 직접 실행하면 봇이 뜨므로 컴파일만 합니다.
log "임포트 점검"
.venv/bin/python -m compileall -q main.py cogs services utils >/dev/null
echo "  통과"

# ── 3. 재시작 ────────────────────────────────────────────────
log "봇 재시작"
if ! systemctl is-enabled --quiet bordo-discord 2>/dev/null; then
  echo "  유닛이 아직 등록되지 않았습니다. 아래를 한 번 실행하십시오."
  echo "    sudo cp $ROOT/deploy/bordo-discord.service /etc/systemd/system/"
  echo "    sudo install -m 0440 -o root -g root \\"
  echo "         $ROOT/deploy/bordo-discord.sudoers /etc/sudoers.d/bordo-discord"
  echo "    sudo systemctl daemon-reload"
  echo "    sudo systemctl enable --now bordo-discord"
  exit 0
fi

# reload 가 아니라 restart 입니다. 봇은 HUP 을 처리하지 않고, reload-or-restart 는
# 유닛 파일이 바뀌어도 ExecStart 를 다시 읽지 않습니다 — 백엔드에서 이것 때문에
# WSGI 가 계속 돌아 500 이 났습니다.
SINCE=$(date '+%Y-%m-%d %H:%M:%S')
sudo systemctl restart bordo-discord

# ── 4. 실제로 Discord 에 붙었는지 ────────────────────────────
#
# is-active 만 보면 안 됩니다. 토큰이 틀려도 프로세스는 잠깐 살아 있어서
# "실행 중" 으로 보이고, 몇 초 뒤 조용히 죽습니다.
#
# 봇은 on_ready 에서 "연결 완료" 를 stdout 으로 찍습니다. 그게 journald 에
# 뜨는지를 봅니다 — 게이트웨이 핸드셰이크가 실제로 끝났다는 유일한 증거입니다.
log "게이트웨이 연결 확인"
for i in $(seq 1 20); do
  if journalctl -u bordo-discord --since "$SINCE" --no-pager 2>/dev/null \
     | grep -q "연결 완료"; then
    echo "  연결됨"
    exit 0
  fi
  if ! systemctl is-active --quiet bordo-discord; then
    echo "중단: 봇이 죽었습니다."
    journalctl -u bordo-discord --since "$SINCE" -n 40 --no-pager
    exit 1
  fi
  sleep 3
done

echo "중단: 60초 안에 Discord 게이트웨이에 붙지 못했습니다."
echo "토큰이 틀렸거나, MESSAGE_CONTENT Intent 가 꺼져 있거나, 네트워크가 막혔습니다."
journalctl -u bordo-discord --since "$SINCE" -n 40 --no-pager
exit 1

#!/usr/bin/env bash
#
# cloudflared 설치.
#
#   sudo ./deploy/install-cloudflared.sh
#
# apt 저장소를 쓰지 않고 바이너리를 직접 받습니다. 이 서버는 apt 소스 설정이
# 깨져 있고(docker 저장소 Signed-By 충돌), 그 설정은 다른 프로젝트가 쓰는 것이라
# 고치지 않기로 했습니다. 바이너리 하나면 apt 를 건드릴 이유가 없습니다.
set -euo pipefail

VERSION="${CLOUDFLARED_VERSION:-latest}"
DEST=/usr/local/bin/cloudflared

case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=amd64 ;;
  armv7l)        ARCH=arm ;;
  *) echo "지원하지 않는 아키텍처: $(uname -m)"; exit 1 ;;
esac

if [ "$VERSION" = "latest" ]; then
  URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}"
else
  URL="https://github.com/cloudflare/cloudflared/releases/download/${VERSION}/cloudflared-linux-${ARCH}"
fi

echo "▸ 내려받기 ($ARCH)"
TMP=$(mktemp)
curl -fsSL --retry 3 -o "$TMP" "$URL"

# 받은 것이 실제로 동작하는지 먼저 봅니다. 깨진 파일을 바로 덮어쓰면
# 기존 설치가 있던 경우 그것까지 못 쓰게 됩니다.
chmod +x "$TMP"
"$TMP" --version >/dev/null

echo "▸ 설치: $DEST"
install -m 0755 "$TMP" "$DEST"
rm -f "$TMP"
"$DEST" --version

# Bordo Discord Bot

AI 협업/대리인 서비스 "Bordo"의 Discord 담당 절반입니다. discord.py로 작성된 단일 파일(`main.py`) 봇으로, Discord 이벤트를 Backend로 릴레이하고 Backend가 결정한 내용을 표시하는 역할을 합니다.

Backend는 아직 존재하지 않습니다. 이 봇은 Backend 호출이 전부 실패하는 상황에서도 계속 동작하도록 설계되어 있습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 환경 변수

`.env` 파일을 만들어 아래 값을 채웁니다.

| 변수 | 필수 | 설명 |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Discord Developer Portal에서 발급받은 봇 토큰 |
| `BORDO_SERVICE_TOKEN` | ✅ | Backend 호출용 인증 시크릿(`DISCORD_TOKEN`과 별개). Backend가 생기기 전까지는 임의 문자열이어도 됩니다 |
| `BORDO_BACKEND_URL` |  | Backend 주소. 기본값 `http://localhost:8000` |
| `BORDO_OUTBOX_INTERVAL` |  | Outbox 폴링 주기(초). 정의만 돼 있고 현재는 사용되지 않습니다 |

## 실행

```bash
python main.py
```

## Discord Developer Portal 설정

- **Privileged Gateway Intent**: `MESSAGE CONTENT INTENT`·`PRESENCE INTENT`·`SERVER MEMBERS INTENT` 활성화 필요 (멤버 인텐트가 없으면 길드 멤버 캐시가 비어 있어 presence 이벤트 자체가 안 옵니다)
- **봇 초대 스코프**: `bot`, `applications.commands` 둘 다 필요 (후자가 빠지면 슬래시 명령이 에러 없이 등록되지 않음)
- **필요 권한**: View Channels, Send Messages, Send Messages in Threads, Embed Links, Create Public Threads, Manage Events, Read Message History

## 슬래시 명령

| 명령 | 설명 |
|---|---|
| `/bordo-connect` | 계정 연결 코드를 DM으로 발급 |
| `/bordo-team` | 현재 연결된 팀 조회 |
| `/meeting-start` | 웹에서 예정된 회의 중 하나를 골라(자동완성) 스레드를 엽니다 |
| `/meeting-end` | 회의 종료(회의 스레드 안에서 실행) |
| `/ask-bordo` | 특정 대리인에게 질문 전달(답변은 비동기) |
| `/delegate-on` / `/delegate-off` | 내 AI 대리인 자동 참석 전역 토글 |

## 아키텍처 원칙

봇은 판단하지 않고 릴레이·표시만 합니다. Discord 이벤트를 Backend로 전달하고, Backend가 결정한 내용만 화면에 표시하는 것이 목표입니다. 회의 요약은 `/meeting-end`가 Backend를 호출한 응답을 그대로 게시할 뿐, 봇이 직접 만들지 않습니다. 봇을 멘션해도 봇이 직접 답하지 않고 그대로 Backend로 넘깁니다.

## 알려진 공백

- Outbox consumer(Backend 큐 폴링 → 실행 → ACK/FAIL) 미구현
- 모든 상태가 인메모리라 재시작하면 진행 중인 회의 정보가 사라짐
- 테스트, 린터, 빌드 단계 없음

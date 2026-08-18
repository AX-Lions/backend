# Bordo Discord Bot

discord.py 단일 파일(`main.py`) 봇.
상위 맥락은 `../CLAUDE.md`와 `../.claude/docs/BordoProgress-v03.md` 참조.

## 실행

```bash
pip install -r requirements.txt
python main.py
```

`.env` 필수값: `DISCORD_TOKEN`, `BORDO_SERVICE_TOKEN`(Backend 호출용, 봇 토큰과 별개).
선택: `BORDO_BACKEND_URL`(기본 `http://localhost:8000`), `BORDO_OUTBOX_INTERVAL`.

Developer Portal: `MESSAGE CONTENT INTENT` 활성화 필수, 초대 스코프는 `bot` +
`applications.commands` **둘 다** (후자가 빠지면 슬래시 명령이 에러 없이 등록 실패).

## 아키텍처 원칙

> **봇은 판단하지 않는다. 릴레이와 표시만 한다.**

Discord 이벤트를 Backend로 전달하고, Backend가 결정한 내용만 화면에 표시한다.
회의 요약은 `/meeting-end`가 Backend를 호출하고 응답에 담겨 온 것을 그대로 게시할 뿐,
봇이 직접 만들지 않는다 (`generate_meeting_wrapup` 제거됨). 봇을 멘션해도 봇이 직접
답하지 않는다 — 그대로 Backend로 넘기면 대리인이 대상 판정까지 한다
(`llm_chat_reply`/`OpenAIService` 제거됨, #17). 여기에 판단 로직을 쌓지 않는다.

**Discord가 회의의 원본이다.** 회의 시작·종료는 `/internal/v1`에만 존재하고,
프론트에는 없다. 이 봇이 회의의 생명주기를 만든다.

## 슬래시 명령

| 명령 | 설명 |
|---|---|
| `/bordo-connect` | 계정 연결 코드를 DM으로 발급 |
| `/bordo-team` | 현재 연결된 팀 조회 |
| `/meeting-start` | 웹에서 예정된 회의 중 하나를 골라(자동완성) 스레드를 엽니다 |
| `/meeting-end` | 회의 종료 (회의 스레드 안에서 실행) |
| `/ask-bordo` | 특정 대리인에게 질문 전달 (답변은 비동기) |
| `/delegate-on` · `/delegate-off` | 내 AI 대리인 자동 참석 전역 토글 |

## Backend 연동 규약

- 인증 헤더: `X-Service-Token` ↔ Backend `settings.BORDO["SERVICE_TOKEN"]`
- **멱등 키: `guild_id + channel_id + message_id`.** 외부 이벤트는 예외 없이 멱등 처리.
  Outbox 유니크 제약은 `(team_id, idempotency_key)`.
- 메시지 전달: Backend가 `Utterance` 저장 → `run_agent_for_utterance()` 호출.
  **봇은 응답을 기다리지 않는다.** 결과는 Outbox로 되돌아온다.
- 회의 상태: `Meeting.status` → ACTIVE / ENDED, `started_at` · `ended_at`
- 참석자 입퇴장: `MeetingParticipant.attendance` 갱신
- 자연어 명령은 `SkillRun` · `SkillStep`으로 실행. **한 단계 실패해도 나머지는 진행**
  (`PARTIALLY_FAILED`). 예: "다음 프론트 회의용 음성 채널 만들어줘" → `CreateVoiceChannel`,
  "수연한테 프론트 역할 추가해줘" → `AssignRole`.
- Backend 호출이 **전부 실패해도 봇은 계속 동작해야 한다.** 이 내성을 깨지 않는다.

## 알려진 공백 (작업 대상)

- `/meeting-start`가 `GET /internal/v1/meetings/scheduled`와 `POST /internal/v1/meetings/start`의
  `meeting_id` 지원을 전제로 짜여 있음(AX-Lions/backend#89). **둘 다 안전하게 비어있는
  상태가 아니다:**
  - `GET /internal/v1/meetings/scheduled`는 아직 없어서 자동완성은 그냥 빈 목록만 돌려준다(안전).
  - `POST /internal/v1/meetings/start`는 이미 존재하지만 옛 계약(agenda/participants 기반
    즉석 생성) 그대로다 — `meeting_id`를 읽지도, `title`/`participants`를 응답에 담지도
    않는다. 지금 이 커맨드를 그대로 배포하면 **실패하지 않고** `project = team.projects
    .order_by("created_at").first()`로 아무 프로젝트나 골라 제목 "Discord 회의", 참석자
    0명짜리 엉뚱한 회의를 조용히 만들어버린다. **#89가 배포되기 전까지 이 브랜치를
    develop 이상으로 배포하지 말 것.**
- 모든 상태가 인메모리 — 재시작하면 진행 중 회의 정보가 사라짐
- 테스트·린터·빌드 단계 없음

## 역할 경계

- 담당은 강다은. Backend 쪽 `/internal/v1` 엔드포인트는 백엔드 담당이 구현한다.
  경로가 없으면 대신 만들지 말고 규약을 맞춘 뒤 대기하거나 스텁으로 진행한다.
- MVP Skill 범위는 아직 미확정 — 늘리기 전에 확인한다.

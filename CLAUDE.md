# Bordo Backend

Django 5 + DRF. 상위 맥락은 `../CLAUDE.md`와 `../.claude/docs/BordoProgress-v03.md` 참조.

## 실행

```bash
python -m venv .venv && .venv\Scripts\activate     # PowerShell
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # 데모 데이터
python manage.py runserver
python scripts/smoke_test.py      # 48개 요청 통합 확인
```

기본 SQLite. PostgreSQL은 `.env`에 `DATABASE_URL`만 넣으면 된다.
데모 계정 비밀번호는 전부 `Bordo!2026` (`susu@bordo.dev`가 팀 OWNER, 회의 불참자).

## 구조

```
config/     settings.py · errors.py(오류 코드 카탈로그) · exceptions.py · urls.py
apps/
  common/     소프트삭제 · 멱등성 · 권한 · 페이징 (공용)
  accounts/   User · 인증
  orgs/       Team · Project · 즐겨찾기 · 최근항목
  agent/      대리인 설정 · 프롬프트 · 대화 · Run · 유보질문
  meetings/   회의 · 참석자 · 안건 · 플로우 · 요약 · 브리핑
  home/       홈 집계
bordo-openapi.yaml   API 스펙 (스펙 170개)
```

## 현재 상태

**스펙 170개 중 40개 구현.** 1차 범위는 **홈 화면 + 플로우 화면**뿐이다.

| 상태 | 영역 |
|---|---|
| 동작함 | 인증 · 팀 · 프로젝트 · 홈 · 회의 CRUD · 플로우 조회 · 대리인 설정 |
| **껍데기** | `ai-briefing`, `pending-questions`, 플로우 엣지 — **시드가 넣은 하드코딩. 생성 코드 없음** |
| 미구현 | Discord 13 · WebSocket · 채팅 21 · 태스크 9 · 캘린더 8 · 문서 7 · 현재상태 15 · MCP 1 · 동기화 4 |

> 껍데기 3종은 API가 응답하므로 동작하는 것처럼 보인다. 특히 **플로우 엣지는 핵심 화면 2개 중 하나의 데이터원**이다.

## 다음 작업 (우선순위)

1. 회의 시작·종료 `/internal/v1/meetings/start|end` — Discord Bot이 호출
2. Discord 자연어 제어 `/internal/v1/discord/commands` + 스킬 실행
3. WebSocket `/ws/projects/{project_id}` — Channels
4. AI 대리인 코어: `services/llm.py` · `react.py` · `policy.py` · `briefing.py` · `tasks.py`
5. Celery + Redis (개발 중엔 `CELERY_TASK_ALWAYS_EAGER=True`로 Redis 없이 동작)

## 코딩 규약

- **오류는 `error.code`로 분기한다.** 코드는 `config/errors.py` 카탈로그에만 존재 —
  정의되지 않은 코드를 쓰면 `BordoError` 생성 시점에 터진다. 새 코드는 카탈로그에 먼저 추가.
- 모든 조회에 `team_id` / `project_id` 필터를 건다.
- 소프트 삭제: `objects`는 살아 있는 행만, `all_objects`가 전부. 기본 매니저를 우회하지 않는다.
- **상태는 PATCH로 바꾸지 않는다.** `Meeting.status` 등은 전용 엔드포인트로만 — 그래야
  `PENDING_APPROVAL`을 건너뛸 수 없다.
- 필터는 파이썬이 아니라 **DB 쿼리로** 내린다 (`content_types` · `surfaces` · `since_minutes`).
  `participant_ids`만 JSON 배열이라 예외.
- 카테고리에 맞지 않는 필터는 빈 배열이 아니라 **400**이다.
- `FlowEdge.compute_opacity()`는 조회 시각이 아니라 **회의 구간** 기준.
- 쓰기 요청은 `Idempotency-Key` 지원 (`apps/common/idempotency.py`). 같은 키·다른 내용은 409.
- AI 산출물은 `PENDING_APPROVAL` / `CANDIDATE` 상태로 생성한다.
- `PRIVATE` 문서는 다른 Agent에게 **존재 여부조차 반환하지 않는다.**

## 역할 경계

- 앱 폴더를 겹치지 않는다 (마이그레이션 충돌).
- 남의 모델(`Meeting`·`Utterance` 등)은 **쓰기만**, 해당 앱 views는 수정하지 않는다.
- ⚠️ `apps/agent/views.py` · `serializers.py`는 건드리지 않는다 — 설정 화면 CRUD, 프론트 연결됨.
- `settings.py` · `urls.py` · `events.py`는 셋이 함께 건드리는 공용 파일. 변경 시 알린다.

## 인터페이스 규약

```python
# apps/agent/tasks.py — Discord 담당(A)이 Utterance 저장 직후 호출. 반환값 없음, 대기 없음.
@shared_task
def run_agent_for_utterance(utterance_id: str) -> None

# apps/common/events.py — 내부에서 transaction.on_commit() 처리
publish(project_id, event_type, payload)
```

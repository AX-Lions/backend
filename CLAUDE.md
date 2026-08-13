# CLAUDE.md

Bordo 백엔드에서 작업할 때 참고하는 문서입니다.
`bordo-openapi.yaml` 이 API 계약의 원본이고, 이 문서는 **그 계약을 코드로 옮길 때의 규칙**입니다.

---

## 이 서비스가 뭘 하는가

시간대가 다른 개발팀이 동시에 접속하지 않아도 회의를 이어가게 하는 도구입니다.
자리를 비운 사람의 **AI 대리인이 대신 회의에 참석**하고, 돌아온 사람은 브리핑과
플로우 그래프로 맥락을 따라잡습니다.

우선순위는 이 순서입니다. 헷갈리면 위쪽을 지키십시오.

1. **회의에서 대리하는 AI 대리인** — 이 서비스의 핵심
2. 프로젝트 팀원을 돕는 AI (팀 관리)
3. 일정 등 일반적인 협업 도구 기능

### 계층

```
User ─┬─ Team ─┬─ Project ─┬─ Document · Meeting · WorkItem · Plan · Thought
      │        │           ├─ Task · CalendarEvent
      │        │           └─ ChatRoom (PROJECT)
      │        ├─ Member · InviteCode · Category
      │        └─ ChatRoom (TEAM)
      ├─ AgentSettings · AgentPrompt      (사용자 단위, 팀 무관)
      └─ ChatRoom (AI · DIRECT · PEER_AGENT)
```

**팀은 조직 단위, 프로젝트는 작업 단위입니다.** 문서·회의·작업이 매달리는 곳은
프로젝트입니다. 반면 **AI 대리인 설정은 사람에게 붙습니다** — 팀마다 다른 대리인을
갖지 않습니다.

---

## 3대 설계 원칙

코드 리뷰에서 이 셋을 깨는 변경은 막습니다.

### 1. 사람 최종 승인

AI 가 만든 태스크·일정·결정은 예외 없이 `PENDING_APPROVAL` / `CANDIDATE` 로
시작합니다. AI 는 후보만 만들고 확정은 사람이 합니다.

구현상 이 원칙은 **상태를 PATCH 로 못 바꾸게** 하는 것으로 지켜집니다.

```python
# ✗ status 를 자유롭게 쓸 수 있으면 PENDING_APPROVAL 을 건너뛸 수 있습니다
PATCH /tasks/{id}  {"status": "TODO"}          # → 400

# ○ 전용 엔드포인트만 전이표를 따라 움직입니다
POST /tasks/{id}/approve                        # PENDING_APPROVAL → TODO
```

`apps/tasks/models.py` 의 `TRANSITIONS` 가 유일한 전이표입니다. 새 상태를 추가하면
거기에만 적으십시오.

### 2. 서버 중심

AI 끼리 직접 통신하지 않습니다. 모든 AI↔AI 메시지는 서버를 거치며
`trace_id` 와 `hop_count`/`max_hops`(기본 3)로 무한 대화를 차단합니다.

같은 맥락에서 **외부 호출(Discord)을 요청 트랜잭션 안에서 하지 않습니다.**
`OutboxEvent` 에 행 하나만 남기고 봇이 가져가 게시합니다. 트랜잭션 안에서
Discord 를 부르면 롤백돼도 메시지는 이미 나가 있습니다.

### 3. 자동 동기화 없음

파일 Watcher 가 없습니다. 충돌 시 자동 병합은 **금지**이며 허용 해결 방식은
`keep_local` / `keep_server` / `keep_both` / `manual_merge` 4가지뿐입니다.

---

## 코드 규약

### 뷰는 함수형 + `@api_view`

ViewSet 을 쓰지 않습니다. 이 서비스의 엔드포인트는 CRUD 모양에서 벗어나는 게
많아(`approve`, `confirm`, `delegate`, `restore`) 라우터가 만들어 주는 것보다
직접 쓰는 게 짧습니다.

```python
@api_view(["GET", "POST"])
def tasks(request, project_id):
    project, member = project_membership(request.user, project_id)
    if request.method == "GET":
        ...
```

### 권한은 `apps/common/permissions.py` 로

뷰 안에서 멤버십을 직접 조회하지 마십시오. 세 함수가 전부입니다.

| 함수 | 쓰는 곳 |
|---|---|
| `team_membership(user, team_id, roles=None)` | 팀 스코프 |
| `project_membership(user, project_id)` | 프로젝트 스코프. `(project, member)` 반환 |
| `meeting_access(user, meeting_id)` | 회의 스코프 |

역할이 필요하면 `roles=(TeamRole.OWNER, TeamRole.ADMIN)` 을 넘깁니다.

### 오류는 `BordoError` 하나로

클라이언트는 **HTTP 상태가 아니라 `error.code` 로 분기**합니다.
새 코드는 `config/errors.py` 의 `ERROR_CODES` 에 먼저 등록하십시오 —
등록 안 된 코드로 `BordoError` 를 만들면 `KeyError` 로 터집니다(의도된 것입니다).

```python
raise BordoError("PROJECT_ACCESS_DENIED",
                 "이 프로젝트 참여자에게만 맡길 수 있습니다.",
                 details={"assignee_id": str(new_id)})
```

`message` 는 **사용자에게 그대로 보여줄 한국어**입니다. 무엇을 해야 하는지까지
적으십시오. `"권한 없음"` 보다 `"먼저 프로젝트에 추가하십시오"` 가 낫습니다.

### 존재를 숨겨야 하는 것은 404

`visibility=private` 문서, 참여자가 아닌 채팅방, 남의 비공개 생각은
**403 이 아니라 404** 입니다. 403 을 주면 "그런 게 있긴 하다"가 새어 나갑니다.

### 날짜는 들어오는 자리에서 파싱

```python
from apps.common.parsing import parse_dt

start_at = parse_dt(request.data.get("start_at"), "start_at", required=True)
```

`Model.objects.create(start_at="2026-09-07T10:00:00+09:00")` 은 DB 에는 들어가지만
**메모리 인스턴스는 문자열 그대로**라 바로 직렬화하면 터집니다.
`refresh_from_db()` 로 덮지 말고 파싱해서 400 으로 돌려주십시오.

### 집계는 서버가

미읽음 합계, 프로젝트 진행률, 참여자별 현지 시각은 전부 서버가 계산해 내려줍니다.
클라이언트가 트리를 순회해 더하면 숨겨진 방을 빠뜨려 숫자가 어긋나고,
시간대 환산은 서머타임 경계에서 값이 갈립니다.

### N+1 은 context 로 막습니다

`is_mine`, `unread_count`, 내 확인 여부처럼 **보는 사람 기준** 값은 serializer 안에서
조회하지 말고 뷰에서 한 번에 모아 context 로 넘깁니다.

```python
ctx = message_context(request.user, rows)      # 한 번에 모음
MessageSerializer(rows, many=True, context=ctx).data
```

### 이벤트는 `publish()` 로만

```python
from apps.common.events import publish
publish(project_id, "task.completed", {"task_id": str(task.id)})
```

내부에서 `transaction.on_commit()` 으로 감쌉니다. 커밋 전에 쏘면 롤백된
트랜잭션의 이벤트가 나가고, 클라이언트는 "생성됐다"를 받고 조회했는데 없는
상황을 만납니다. 2단계는 로그만 남기며 3단계에서 Channels 로 교체됩니다 —
**호출부는 한 줄도 안 바뀝니다.**

### 삭제 방식은 대상마다 다릅니다

| 방식 | 대상 | 이유 |
|---|---|---|
| 소프트 삭제 (30일) | 팀 · 프로젝트 · 문서 · 회의 · 태스크 · 계정 | 되돌릴 수 있으면 사고가 사고로 안 끝납니다 |
| 내용만 비움 | 채팅 메시지 | 자리를 없애면 앞뒤 맥락이 끊깁니다 |
| 내 목록에서만 숨김 | `DIRECT` · `PEER_AGENT` 방 | 한쪽이 지운다고 상대 기록까지 사라지면 안 됩니다 |
| 하드 삭제 | 알림 · work/plan/thought · 첨부 | 휘발성이거나 개인 자산 |

**삭제해도 Agent Run 의 Evidence 스냅샷은 남습니다.** 그때 대리인이 무엇을 보고
답했는지가 사라지면 추적성이 끊깁니다.

### 주석은 "왜"만

무엇을 하는지는 코드가 말합니다. 주석에는 **왜 이렇게 했는지**, 특히
**다르게 했을 때 뭐가 깨지는지**를 적으십시오.

```python
# ✗ 진행률을 다시 계산한다
# ○ 승인 대기를 분모에서 빼는 이유 — AI 가 후보를 열 개 만들면 진행률이
#   갑자기 떨어집니다. 사람이 승인해서 실제 할 일이 된 것만 셉니다.
```

---

## 앱 구조와 담당

**앱 폴더를 넘나들지 마십시오.** 마이그레이션이 충돌합니다.

| 앱 | 내용 | 담당 |
|---|---|---|
| `apps/common` | 베이스 모델 · 권한 · 페이징 · 이벤트 · 파싱 | 공용 |
| `apps/accounts` | 계정 · 인증 | 기반 |
| `apps/orgs` | 팀 · 프로젝트 · 초대 · 즐겨찾기 | 기반 |
| `apps/meetings` | 회의 · 플로우 그래프 · 안건 | 기반 |
| `apps/home` | 홈 집계 | 기반 |
| `apps/agent` | 대리인 설정 · 프롬프트 · 대화 · Run | **B** (AI) |
| `apps/chat` | 채팅 5종 · 중요 · 첨부 · 달력 | **D** (API) |
| `apps/states` | work · plan · thought · 활동 로그 | **D** |
| `apps/tasks` | 태스크 · 상태 전이 · 진행률 | **D** |
| `apps/calendars` | 일정 · 리마인더 · Outbox | **D** |
| `apps/documents` | 문서 · 버전 · 비밀키 마스킹 | **D** |
| `apps/discord` | 봇 연동 · 스킬 | **A** (예정) |

### 남의 앱을 건드릴 때

- **모델은 읽고 써도 됩니다.** FK 로 참조하는 건 정상입니다.
- **views.py · serializers.py 는 고치지 마십시오.** 특히 `apps/agent/views.py` 는
  설정 화면 CRUD 로 프론트가 이미 붙어 있습니다.
- 남의 앱에 기능이 필요하면 **내 앱에서 지연 생성**하는 쪽을 먼저 보십시오.
  예: 프로젝트 단체 채팅방은 `orgs` 를 고치는 대신 `chat/services.py` 의
  `ensure_project_room()` 이 조회 시점에 채웁니다. 이미 만들어진 프로젝트에도
  방이 생긴다는 이점이 덤으로 따라옵니다.

### `config/urls.py`

구역이 주석으로 나뉘어 있습니다. **자기 구역 끝에 붙이십시오.**
남의 구역에 끼워 넣으면 머지에서 부딪힙니다.

---

## 개발 환경

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo        # susu@bordo.dev / Bordo!2026
.venv/bin/python manage.py runserver
```

기본 DB 는 SQLite 입니다. `DATABASE_URL` 이 있으면 PostgreSQL 을 씁니다
(운영은 pgvector 때문에 PostgreSQL 전제).

### 검증

```bash
.venv/bin/python manage.py check
.venv/bin/python scripts/smoke_test.py      # 기반 (인증 · 팀 · 회의 · 플로우)
.venv/bin/python scripts/smoke_crud.py      # D 담당 (채팅 · 상태 · 태스크 · 캘린더 · 문서)
```

스모크 테스트는 커버리지가 목적이 아닙니다. **정상 경로 한 번, 그리고 막아야 하는
경로를 눌러 실제로 막히는지** 봅니다. 새 규칙을 넣었으면 "이건 400 이어야 한다"를
한 줄 추가하십시오.

---

## 커밋 컨벤션

Conventional Commits 를 쓰되 **본문은 한국어**로 적습니다.

```
<type>(<scope>): <제목 — 명령형, 50자 이내, 마침표 없음>

무엇을 왜 바꿨는지. 특히 **다르게 했으면 뭐가 깨지는지.**
- 목록으로 나눠도 됩니다
- 계약(bordo-openapi.yaml)과 다르게 구현했으면 반드시 이유를 남기십시오
```

### type

| type | 쓰는 곳 |
|---|---|
| `feat` | 새 기능 · 새 엔드포인트 |
| `fix` | 버그 수정 |
| `refactor` | 동작 그대로 구조만 |
| `docs` | 문서 · 주석 |
| `test` | 스모크 · 테스트 |
| `chore` | 설정 · 의존성 · 마이그레이션 정리 |

### scope

앱 이름을 씁니다 — `chat`, `tasks`, `calendars`, `documents`, `states`,
`meetings`, `orgs`, `agent`, `common`, `config`.
여러 앱에 걸치면 생략합니다.

```
feat(tasks): 상태 전이를 전용 엔드포인트로 분리

PATCH 로 status 를 쓸 수 있으면 PENDING_APPROVAL 을 건너뛰고 바로 TODO 로
넣을 수 있어 설계 1원칙(사람 최종 승인)이 무력해집니다.

- TRANSITIONS 전이표를 단일 출처로
- PATCH 에 status 가 오면 400 + 쓸 엔드포인트 안내
- AI 후보는 삭제 대신 reject 를 쓰도록 409
```

### 하지 말 것

- 마이그레이션 파일을 손으로 고치기 (`makemigrations` 로 다시 만드십시오)
- 여러 앱의 무관한 변경을 한 커밋에
- `db.sqlite3` · `.env` · `.venv/` 커밋 (`.gitignore` 에 있습니다)

---

## 브랜치 · PR

### 브랜치

```
main                 배포 기준
└─ develop           통합
   ├─ feat/api-crud       D — 채팅 · 상태 · 태스크 · 캘린더 · 문서
   ├─ feat/discord        A
   └─ feat/agent-runtime  B
```

작업 브랜치는 **`develop` 에서 따고 `develop` 으로 PR** 합니다.
`main` 직접 푸시는 하지 않습니다.

브랜치 이름: `<type>/<짧은-영문-슬러그>` — `feat/chat-attachments`, `fix/outbox-retry`

### PR 본문

```markdown
## 무엇을
한 문단. 어느 화면·플로우가 이걸로 열리는지.

## 왜 이렇게
계약과 다르게 간 부분이 있으면 여기 적습니다.
"OpenAPI 에는 X 인데 Y 로 했다. X 로 하면 <이런 게> 깨진다."

## 확인한 것
- [ ] `manage.py check` 통과
- [ ] `scripts/smoke_*.py` 통과
- [ ] 마이그레이션이 새로 생겼다면 파일명 명시
- [ ] 남의 앱 views/serializers 안 건드림

## 남은 것
다음 사람이 이어받을 자리. 없으면 `없음`.
```

### 리뷰에서 보는 것

1. 3대 원칙을 깨지 않는가 (특히 상태 전이)
2. 권한 검사가 `apps/common/permissions.py` 를 거치는가
3. 숨겨야 할 것에 403 대신 404 를 주는가
4. 목록 응답에 N+1 이 있는가
5. 외부 호출이 트랜잭션 안에 들어갔는가
6. 주석이 "무엇"이 아니라 "왜"를 적었는가

---

## 계약과 구현이 다른 곳

`bordo-openapi.yaml` 을 그대로 따르지 않은 부분입니다. **바꿀 때는 여기도 갱신하십시오.**

| 계약 | 구현 | 이유 |
|---|---|---|
| `ChatMessage.important_confirmed_at` 스칼라 | `MessageImportance(message, user)` 별도 테이블 | 단체방에서 한 사람이 확인하면 다른 사람 목록에서도 사라집니다. 응답 필드명은 그대로 두되 **값은 요청자 기준**입니다 |
| 메시지 읽음 | `RoomMember.last_read_at` 워터마크 | 메시지별 읽음 행은 단체방 10명 × 1만 건에 10만 행이 됩니다 |
| 첨부 상태 없음 | `UPLOADED / ATTACHED / EXPIRED` + TTL | 보내다 만 파일이 영원히 쌓입니다 |
| `date` 와 `before` 동시 허용 | 상호 배타 (400) | 달력 이동과 스크롤은 기준점이 달라 서버·클라이언트 해석이 갈립니다 |
| Outbox 조회 없음 | `GET /outbox-events/{id}` + `retry` | 공지 실패를 사용자가 볼 방법이 없으면 공지가 나간 줄 알고 기다립니다 |
| 일정 확정과 회의 상태 분리 | 확정이 회의 상태도 올림 | `일정은 확정인데 회의는 예정` 은 사용자가 해석할 수 없습니다 |
| 태스크 `start` · `block` 없음 | 추가 | `TODO → IN_PROGRESS` 경로가 없어 화면이 막힙니다 |
| 문서 비밀키 마스킹 시점 미정 | 저장 **전** | 색인에 들어간 뒤 지우면 이미 검색 결과와 대리인 발언에 실려 나간 뒤입니다 |

---

## 알려진 미구현

- **`Idempotency-Key` 가 동작하지 않습니다.** `apps/common/idempotency.py` 의
  `IdempotencyRecord` 는 `models.py` 가 아니라 별도 파일에 있어 Django 가 모델로
  등록하지 않습니다. 마이그레이션도 없고 `IdempotentCreateMixin` 을 쓰는 뷰도
  없습니다. 계약상 약 40개 엔드포인트가 이 헤더를 받는다고 돼 있으니
  **중복 방지가 필요한 곳은 도메인 멱등 키로 직접 막으십시오** —
  채팅은 `client_message_id`, Outbox 는 `(team, idempotency_key)` 유니크,
  리마인더는 `(event, notification_type)` 유니크로 처리했습니다.
- 임베딩 검색은 2차입니다. 문서 검색은 지금 `icontains` 입니다.
- `DailyChatSummary` 는 빈 껍데기입니다. 조회 계약만 확정해 뒀고 생성기는 B 담당입니다.
- MCP(`/mcp`)와 동기화는 2차 범위라 없습니다.

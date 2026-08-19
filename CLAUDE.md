# CLAUDE.md

Bordo 백엔드에서 작업할 때 참고하는 문서입니다.
`bordo-openapi.yaml` 이 API 계약의 원본이고, 이 문서는 **그 계약을 코드로 옮길 때의 규칙**입니다.

Django 5 + DRF. 상위 맥락은 `../CLAUDE.md` 와 `../.claude/docs/BordoProgress-v03.md`,
저장소 공통 협업 규칙은 `CONTRIBUTING.md` 를 보십시오.

## 이 저장소에는 서비스가 둘 있습니다

`bot/` 은 Discord 봇입니다(discord.py). 원래 `AX-Lions/discord` 였는데 제출
저장소 수 제한으로 합쳤습니다 — 이력은 `git log -- bot/` 로 그대로 볼 수 있습니다.

**둘은 다른 프로세스입니다.** 의존성·가상환경·배포·systemd 유닛이 전부 따로고,
서버에도 **체크아웃을 둘** 둡니다(`bordo/backend` · `bordo/bot-repo`). 한 곳을
둘이 쓰면 두 배포가 같은 디렉터리에서 `git reset --hard` 를 해 서로를 앞 커밋으로
되돌립니다.

- **`bot/` 안의 파일은 Discord 담당(강다은)의 영역입니다.** 합쳤다고 경계가
  사라지지 않습니다 — 대회가 역할 분배를 봅니다.
- 봇과 서버는 서로를 직접 부르지 않습니다. `/internal/v1` 과 Outbox 로만 오갑니다.
- `requirements.txt` 를 합치지 마십시오. Django 와 discord.py 는 다른 프로세스이고,
  합치면 버전 충돌을 서로에게 떠넘기게 됩니다.

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

같은 이유로 `PRIVATE` 문서는 다른 Agent 에게 **존재 여부조차 반환하지 않습니다.**

### 조회에는 스코프 필터를 반드시 건다

모든 조회에 `team_id` / `project_id` 필터를 겁니다. 소프트 삭제는 매니저가
갈라 줍니다 — `objects` 는 살아 있는 행만, `all_objects` 가 전부입니다.
**기본 매니저를 우회하지 마십시오.**

### 필터는 파이썬이 아니라 DB 쿼리로

`content_types` · `surfaces` · `since_minutes` 는 전부 쿼리로 내립니다.
`participant_ids` 만 JSON 배열이라 예외입니다.

카테고리에 맞지 않는 필터가 오면 빈 배열이 아니라 **400** 입니다. 빈 배열을 주면
"조건에 맞는 게 없다"와 "조건 자체가 틀렸다"를 클라이언트가 구별하지 못합니다.

`FlowEdge.compute_opacity()` 는 조회 시각이 아니라 **회의 구간** 기준입니다.
조회 시각으로 하면 같은 회의를 내일 열었을 때 그림이 달라집니다.

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
| `apps/calendars` | 일정 · 리마인더 · 공지 **요청**(발송 아님) | **D** |
| `apps/documents` | 문서 · 버전 · 비밀키 마스킹 | **D** |
| `apps/discord` | 봇 연동(`views.py`, 서비스 토큰) · **웹 쪽 연결 코드 입력·상태 진단**(`web_views.py`, JWT) | **A** / 웹 쪽은 기반 |
| `apps/mcp` | `/mcp` (개인 AI 클라이언트) · `brd_` 토큰 · 쓰기 도구 3종 | 재민 |

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

`config/settings.py` · `config/urls.py` · `apps/common/events.py` 셋은 A·B·D 가
함께 건드리는 공용 파일입니다. **고쳤으면 알리십시오.**

### 담당 사이 인터페이스

앱을 넘나들지 않는 대신 아래 두 지점으로 붙습니다. 시그니처를 바꾸면 상대 쪽이
말없이 깨지므로 바꾸기 전에 알립니다.

```python
# apps/agent/tasks.py — A(Discord)가 Utterance 저장 직후 호출. 반환값 없음, 대기 없음.
@shared_task
def run_agent_for_utterance(utterance_id: str) -> None

# apps/common/events.py — 내부에서 transaction.on_commit() 처리
publish(project_id, event_type, payload)
```

### Outbox — 서버가 쓰고 봇이 가져갑니다

`apps/agent/models.py` 의 `OutboxEvent` 입니다. **외부 호출을 요청 트랜잭션 안에서
하지 않기 위한 장치**입니다 — 롤백돼도 Discord 메시지는 이미 나가 있습니다.
행 하나만 남기고 봇이 폴링해 게시한 뒤 결과를 돌려줍니다.

| 필드 | 값 | 비고 |
|---|---|---|
| `team` | FK | |
| `idempotency_key` | str(120) | **`(team, idempotency_key)` 유니크** |
| `kind` | `MESSAGE` · `ANNOUNCEMENT` · `DM` | |
| `channel_id` | str(40) | 봇이 그대로 씁니다. 서버는 해석하지 않습니다 |
| `payload` | JSON | |
| `status` | `PENDING` · `SENT` · `FAILED` · `DEAD` | |
| `attempts` / `max_attempts` | int | 기본 상한 5 |
| `available_at` | datetime | **이 시각 전에는 가져가지 않습니다** |
| `last_error` | text | 2000자에서 자릅니다 |
| `run` | FK → `AgentRun` | nullable |

봇의 폴링 쿼리는 이 모양입니다. 인덱스가 여기에 맞춰져 있습니다.

```python
OutboxEvent.objects.filter(status="PENDING", available_at__lte=now).order_by("available_at")
```

결과 반영은 모델 메서드로 합니다.

```python
event.mark_sent()              # 성공
event.mark_failed("오류 내용")   # 실패 → 지수 백오프 후 PENDING, 상한 초과 시 DEAD
```

**`DEAD` 는 사람이 봐야 하는 상태입니다.** 무한 재시도는 같은 오류를 반복하고,
그냥 버리면 사용자는 대리인이 말한 줄 알고 있습니다.

멱등키는 방향에 따라 규칙이 다릅니다.

```
나가는 쪽 (서버 → 봇)   run_id 나 발언 식별자
들어오는 쪽 (봇 → 서버)  guild_id + channel_id + message_id
```

> `/internal/v1` 의 폴링·ACK 엔드포인트는 **A 담당**입니다. 모델과 계약을 먼저 만들어
> 두었으니 양쪽이 독립적으로 진행할 수 있습니다. 스키마를 바꿔야 하면 알려 주십시오.

---

## 개발 환경

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo        # susu@bordo.dev / Bordo!2026
.venv/bin/python manage.py runserver
```

Windows PowerShell 이면 앞의 두 줄 대신 이렇게 합니다. 이하 `.venv/bin/python` 은
전부 `.venv\Scripts\python` 으로 읽으십시오.

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
```

기본 DB 는 SQLite 입니다. `DATABASE_URL` 이 있으면 PostgreSQL 을 씁니다
(운영은 pgvector 때문에 PostgreSQL 전제).
데모 계정 비밀번호는 전부 `Bordo!2026` 이고 `susu@bordo.dev` 가 팀 OWNER 이자
회의 불참자입니다 — 브리핑·불참 뱃지를 볼 때 이 계정으로 보십시오.

Celery 는 개발 중 `CELERY_TASK_ALWAYS_EAGER=True` 로 Redis 없이 동작시킵니다.

### 개인 AI 붙여 보기 (MCP)

```bash
# 1. 웹 로그인 JWT 로 토큰 발급 — 응답의 setup_command 를 그대로 붙여 넣습니다
curl -X POST http://localhost:8000/api/v1/me/mcp-token -H "Authorization: Bearer <jwt>"
# 2. Claude Code 에서
claude mcp add --transport http bordo http://localhost:8000/mcp --header "Authorization: Bearer brd_..."
# 3. /mcp → bordo → connected, 도구 3개. "MCP 연동 붙었어. 기록해 줘" → bordo_record_work
```

토큰은 **`.mcp.json`(저장소에 커밋됨)에 넣지 마십시오.** `claude mcp add` 기본 위치(개인 설정)면 됩니다.
`BORDO_PUBLIC_URL` 을 두면 `setup_command` 의 주소가 그 값으로 찍힙니다.

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

## 디자인이 계약을 이겼던 곳

와이어프레임 실물을 보고 **계약 쪽을 고친** 부분입니다. 화면과 API 가 갈리면
화면이 원본입니다 — 사용자가 보는 건 화면이지 스키마가 아닙니다.

| 무엇 | 이전 | 지금 | 근거 |
|---|---|---|---|
| 플로우 화살표 | 전달 1건 = 화살표 1개 | 사람 쌍마다 1개 + 종류별 개수 뱃지 (`arrows[].counts`) | 낱개를 그리면 두 사람 사이에 선이 열 개 겹칩니다 |
| 콘텐츠 종류 | 회의 모드 3종 (`REVISION` 포함) | 6종 — 의견·요청사항·변동사항·**일정**·**결론**·**기타** | 필터 칸이 6개이고 화살표에 `일정` 뱃지가 실제로 붙어 있습니다 |
| `REVISION` | 수정사항 | `CHANGE` 변동사항 | 화면 라벨과 중앙 요약표 헤더가 모두 `변동 사항` |
| 브리핑 | narrative + used/deferred + needs_answer | **4섹션** + 정보 위치 칩 + 검색 | `확인이 필요해요` · `나에게 요청한 내용` 이 계약에 아예 없었습니다. **구현은 B 담당** — 명세만 고쳐 뒀습니다 |
| 홈 인사 | 문구를 교체 | 문구는 두고 **버튼을 추가** | 이름으로 맞이하는 인사가 사라지면 첫 화면 인상이 바뀝니다 |
| 최근 회의 카드 | `main_agendas` · `main_opinions` | `main_decisions` · `zero_summary` · `missed` | 화면이 묻는 건 "무엇을 다뤘나"가 아니라 "무엇이 정해졌나" |
| 화면에 찍히는 문자열 | 클라이언트가 ISO 를 포맷 | 서버가 완성해 내려줌 (`displayed_at` · `time_range` · `status` · `location` · `meta`) | 브라우저 시간대로 찍으면 같은 회의를 사람마다 다른 시각으로 봅니다 |
| 대리인 호칭 | `AI 대리인` | **`{이름}의 Bordo`**, 화자일 때 **`Zero`** | `AI 대리인` 은 화면 어디에도 없는 낱말입니다 |
| MCP 도구·토큰 이름 | `deputy_` · `dpt_` | **`bordo_` · `brd_`** | 도구 이름은 사용자 AI 대화창에 그대로 찍힙니다. 서비스 이름이 Bordo 입니다 |
| 회의 전 준비 | `prebrief`(생성만) + `preanswers`(정규식 패턴) | **`/absence` → `/prep`** · `DebatePoint` + `DebateStance` | `prebrief` 는 만들라는 오퍼레이션만 있고 **결과를 꺼낼 경로가 스펙에 없었습니다.** `preanswer` 는 `pattern` 기반이라 논쟁점에 묶을 필드도 수정·삭제도 없는데, 화면은 카드마다 ⋮ 메뉴를 요구합니다 |
| 자료 공개 스위치 | 한 칸(`disclose_work_plan_thought`) | **작업 · 계획 · 생각 셋** | 화면에는 스위치가 셋인데 모델이 한 칸이라 어느 것을 눌러도 셋이 함께 움직였습니다 |
| 대리 참석 뱃지 | 없음 | `{이름}의 Bordo 대리 참석 예정` 을 서버가 조립 | 진입점이 홈과 Discord 팝업 둘이라, 클라이언트가 만들면 어디서 눌렀느냐에 따라 문구가 갈립니다 |

`used_answers` / `deferred_answers` 는 화면에서 빠졌지만 **응답에는 남깁니다** —
대리인이 무엇을 근거로 답했는지는 추적성 자산이라 화면에 없다고 지우면 안 됩니다.

### 담당이 갈리는 지점

**명세는 화면을 따라 고쳤지만, 구현이 D 몫이 아닌 것들이 있습니다.**

| 명세에 있는 것 | 구현 담당 | D 가 안 만든 이유 |
|---|---|---|
| `AiBriefing` 4섹션 · `/briefing-confirmations/…` · `/briefing-requests/…` | **B** | 브리핑은 대리인 산출물입니다 |
| Outbox(발송함) · `/outbox-events/…` · ACK · 재시도 | **A** | Discord 발송 큐입니다 |

`apps/calendars` 는 공지를 **요청만** 합니다 — `discord_notified` 표시를 올리고
`calendar.discord.announcement_requested` 이벤트를 `publish()` 로 흘립니다.
A 가 발송함을 붙이면 그 이벤트를 받아 큐에 넣습니다. 페이로드에 멱등 키를
같이 실어 보내므로 같은 일정이 두 번 공지되지 않습니다.

`나에게 요청한 내용` 을 바로 태스크로 안 만드는 이유는, 곧바로
`PENDING_APPROVAL` 로 찍으면 승인 큐가 남의 회의 요청으로 가득 차
승인이라는 행위가 뜻을 잃기 때문입니다 — B 가 accept 를 구현할 때
`TODO` 로 만들면 됩니다(사람이 직접 받은 것이라 승인 단계가 필요 없습니다).

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
| 채팅 날짜를 서버 시간대로 | **요청자 `User.timezone`** 으로 자름 (`active-dates` · `?date=` · `search.date` · `daily-summary`) | 서버는 UTC 라 한국에서 자정 넘어 보낸 말이 전날로 묶였습니다. 화면은 브라우저 시간대로 구분선을 그리므로 그 구분선을 눌러도 그 메시지가 안 나옵니다 |
| 대리인 방 제목이 저장값 | 조회 시점에 `agent_display_name()` 으로 조립 | 개인 설정에서 이름을 바꿨는데 방 제목만 옛 이름이면 저장이 안 된 줄 압니다. 어차피 이름을 못 바꾸는 방입니다 |
| `indexes` 가 회의 스코프 | `category=WORK` 은 **프로젝트 + 기간**, 묶는 값은 `FlowEdge.work_document` | 작업 엣지에는 회의가 없어 회의로 좁히면 언제나 빈 배열입니다. 기존 `document` 는 회의 문서 스냅샷이라 작업 엣지에 못 씁니다 |
| `/internal/v1/discord/presence` 는 사람 상태만 | `discord_user_id` 없이 오면 **봇 생존 신호**로 캐시(202) | 봇의 `on_ready` 가 `{status, at}` 만 보내 매번 400 이었습니다. 이 신호로 `GET /teams/{id}/discord/status` 가 봇이 살아 있는지 보여줍니다 |
| `discord/status` 가 Intent·권한 진단 | 서버 연결 · 봇 생존 · 계정 이은 팀원 수 · warnings | Intent·권한은 봇만 압니다. 서버가 아는 것만 정직하게 냅니다 |
| `ai-briefing` 조회 = 읽음 | `?mark_read=false` 로 끌 수 있음 (기본은 읽음) | 플로우 화면이 패널을 열든 말든 부르므로, 회의에 잠깐 들른 것만으로 홈 브리핑 버튼이 사라졌습니다 |
| `/mcp` 도구 13종 · `initialize` 만 | **쓰기 3종** (`bordo_record_work` · `bordo_upload_document` · `bordo_complete_work`) · **dual-era** (legacy `initialize` + modern `server/discover`) · 도구 실행 오류는 `result.isError` | 읽기 도구가 없어야 장기 토큰이 새도 가져갈 게 없습니다. 한쪽 세대만 받으면 클라이언트에 따라 연결이 안 됩니다. `docs/decisions/2026-08-18-mcp-범위.md` |
| MCP Tool 인자가 `team_id` | `project_id` (생략 시 최근 프로젝트, 결과에 `resolved_by`) | 문서·work 는 프로젝트에 매달립니다. 팀만으로는 어디에 쓸지 정할 수 없습니다 |
| 불참과 대리 참석이 별개 | **같은 행위** — `/absence` 하나가 `delegated=True` + `attendance=DELEGATED` | `delegated=False` 로 두면 회의 후 브리핑이 통째로 안 생깁니다(브리핑은 대리 참석자에게만). 없는 사이 무슨 일이 있었는지 못 봅니다 |
| 회의를 봇이 즉석 생성 | **웹에서 만든 회의에 스레드만 붙임** (`meeting_id`). 못 찾으면 404 이고 **절대 즉석 생성으로 안 빠집니다** | 웹이 원본이고 Discord 는 회의 공간입니다. 예전에는 `meeting_id` 를 아예 안 읽어, 봇이 보내도 무시하고 아무 프로젝트에 제목 `Discord 회의` 짜리 참석자 0명 회의를 만들었습니다 (이슈 #89) |
| 봇 경로가 `ABSENT`, 웹이 `DELEGATED` | **둘 다 `DELEGATED`** | 같은 행위가 경로에 따라 다른 값이면 홈 카드 뱃지가 어디서 눌렀느냐에 따라 갈립니다 |
| 회의별 설정 없음 | `MeetingParticipant.settings_override` · `prompt_override` | 회의 하나 때문에 평소 설정을 바꿔 두면 끝난 뒤 되돌리는 것이 사람 몫이 되고 대개 잊습니다 |
| 논쟁점이 사람마다 | **회의에 답니다** (입장만 사람별) | 쟁점은 회의의 성질입니다. 사람마다 만들면 같은 예측을 두 번 돌리고, 둘이 서로 다른 쟁점을 보게 되어 회의 후에 말이 안 맞습니다 |

---

## 현재 상태

명세는 오퍼레이션 **185개**(`bordo-openapi.yaml` `0.0.7`), `config/urls.py` 에 라우트
**122개**가 등록돼 있습니다.

| 상태 | 영역 |
|---|---|
| 동작함 | 인증 · 팀 · 프로젝트 · 홈 · 회의 CRUD · 플로우(조회 **+ 생성**) · 대리인 설정 · 채팅 · 현재 상태 · 태스크 · 캘린더 · 문서 · Discord `/internal/v1` · WebSocket · 대리인 코어(ReAct · POLICY · 유보 · 브리핑) · **MCP 1단계** (`/mcp` 쓰기 3종 · `brd_` 토큰) · **회의 대리 참석 준비**(불참 등록 · 논쟁점 예측 · 입장 · 회의별 설정) |
| 미구현 | MCP 2단계(읽기 · 동기화 · 대리인 질문 10종) · 동기화 4 · `Idempotency-Key` · 문서 임베딩 검색 · `DailyChatSummary` 생성기 |

> **껍데기 3종은 없어졌습니다.** `ai-briefing` · `pending-questions` · 플로우 엣지 모두
> 생성 코드가 붙었습니다 — 시드 없이 새 회의를 열어도 화면이 채워집니다.
> 플로우 진하기는 **회의가 끝날 때** 채워진다는 점만 기억하십시오
> (`flow.recompute_for_meeting()`). 진행 중에는 전부 1.0 입니다.

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
- MCP 는 **쓰기 3종만** 있습니다. 읽기·동기화·`bordo_ask_agent` 는 2단계이며, 읽기를 붙일 때는
  **토큰 만료**가 같이 와야 합니다 (`apps/mcp/tools/write.py` 머리말).

---

## 다음 작업 (우선순위)

**1차 범위는 끝났습니다.** 아래는 남은 구멍과 2차 범위입니다.

1. `masked_secrets` 가 제목만 PATCH 해도 초기화됩니다 (이슈 #7, D 담당)
2. 유보가 `기타`(`ETC`) 로 들어갑니다. 화면 필터 6종에 `유보` 칸이 없어서인데,
   디자인에 칸이 생기면 종류를 나누는 게 맞습니다
3. `FlowEdge` 의 `agenda` · `document` 연결이 비어 있습니다. 안건 자동 매칭이 붙으면
   좌측 인덱스에서 화살표로 점프할 수 있습니다
3-1. Discord 회의 시작 팝업(10초 뒤 자동 대리)은 **봇 쪽 몫**입니다. 서버는
   `GET /internal/v1/meetings/participants` 로 물어볼 대상(`asked=false`)을 알려주고
   `POST /internal/v1/meetings/absence` 로 결과를 받습니다. 타이머를 서버가 들면
   봇이 죽어 있을 때 **아무에게도 안 물어보고 대리 처리**됩니다
4. `DailyChatSummary` 생성기 (B 담당)
5. `Idempotency-Key` — 지금은 도메인 멱등 키로만 막고 있습니다 (아래 「알려진 미구현」)
6. MCP 2단계 — 읽기 도구 + 토큰 만료 · 동기화 4 · `bordo_ask_agent`
7. 문서 임베딩 검색 — 2차. 지금은 `icontains` 입니다

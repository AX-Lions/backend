# Bordo — 백엔드 · Discord 봇

AI 협업 대리인 서비스.

**이 저장소에는 서비스가 둘 들어 있습니다.**

| 폴더 | 무엇 | 무엇으로 |
|---|---|---|
| 저장소 루트 | 웹·API·대리인 | Django 5 + DRF |
| [`bot/`](bot/) | Discord 봇 | discord.py |

**둘은 다른 프로세스입니다.** 의존성도(`requirements.txt` · `bot/requirements.txt`)
가상환경도 배포도 따로입니다. 서로를 직접 부르지 않고 `/internal/v1` 로만 오갑니다.

> `bot/` 은 원래 `AX-Lions/discord` 저장소였습니다. 제출 저장소 수 제한으로
> 여기 합쳤고, 커밋 이력은 **작성자·날짜 그대로** 옮겼습니다 — `git log -- bot/`.
>
> **PR 과 이슈는 따라오지 않아 원본을 그대로 둡니다** —
> [AX-Lions/discord](https://github.com/AX-Lions/discord) (PR 17건 · 이슈 11건).
> 봇 쪽 리뷰 기록을 보려면 그쪽을 보십시오.

---

**1차 범위는 홈 화면과 플로우 화면입니다.** 회의록에서 "이 페이지가 잘 나와야 한다"고 정한
그 두 화면을 먼저 돌아가게 만들었습니다. 채팅·태스크·캘린더·MCP·동기화는 2차입니다.

---

## 3분 만에 띄우기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo          # 데모 데이터
python manage.py runserver
```

```bash
# 잘 도는지 한 번에 확인 (48개 요청)
python scripts/smoke_test.py
```

기본은 SQLite 입니다. PostgreSQL 로 가려면 `.env` 에 `DATABASE_URL` 만 넣으면 됩니다.

### 데모 계정

| 이메일 | 이름 | 비고 |
|---|---|---|
| `susu@bordo.dev` | 유수인 | 팀 OWNER. **끝난 회의에 불참**해서 브리핑이 쌓여 있습니다 |
| `backend01@bordo.dev` | 최비성 | |
| `front01@bordo.dev` | 임수연 | |
| `jaemin@bordo.dev` | 서재민 | |

비밀번호는 전부 `Bordo!2026` 입니다.

### 관리자 화면

```bash
python manage.py createsuperuser
# http://localhost:8000/admin/
```

회의·플로우 엣지를 눈으로 보고 고칠 수 있어서 시연 준비할 때 편합니다.

---

## 구현된 것

### 00. 홈 — 요청 한 번으로 첫 화면 전체

```
GET  /api/v1/home
POST /api/v1/me/briefing-dismiss
PUT  /api/v1/meetings/{id}/favorite
```

`GET /home` 하나가 환영 문구 · 최근 회의 카드 5개 · 오늘 일정 · 최근 회의 요약 ·
프로젝트 진행 현황 · 사이드바(최근 항목 / 즐겨찾기)를 전부 돌려줍니다.
따로 부르면 첫 진입에서만 왕복이 6번 생깁니다.

디자인의 **`불참한 회의` 뱃지**는 `recent_meetings[].missed` 입니다.
`오늘 일정` 의 `channel: "Discord"` 는 회의가 어디서 열리는지 표시하는 값입니다.

### 09. 회의 · 플로우 — 서비스의 차별점

```
GET /api/v1/meetings/{id}/flow?category=&participant_ids=&content_types=&surfaces=&since_minutes=
GET /api/v1/meetings/{id}/indexes?category=
GET /api/v1/meetings/{id}/summary-table
GET /api/v1/meetings/{id}/context
GET /api/v1/meetings/{id}/agendas
GET /api/v1/flow-edges/{id}
    /api/v1/me/flow-filters      (프리셋 CRUD)
```

**플로우 그래프는 `flow_edge` 한 테이블만 읽으면 그려집니다.** 노드 이름과 방향 표기
(`AI 대리인 → A, B`)가 행 안에 들어 있어 사용자 테이블을 조인하지 않습니다.

### 10. AI 대리인

```
GET  /api/v1/meetings/{id}/ai-briefing
GET  /api/v1/meetings/{id}/pending-questions
     /api/v1/me/agent/settings          (O/X 4종)
     /api/v1/me/agent/prompts           (프롬프트 카드 CRUD)
     /api/v1/me/agent/conversations
```

### 01~03. 인증 · 팀 · 프로젝트

JWT 발급·갱신, 팀 CRUD, 초대 코드, 프로젝트 CRUD, 즐겨찾기, 최근 항목.

---

## 설계에서 신경 쓴 것

### 오류는 코드로 분기합니다

모든 4xx·5xx 가 같은 형식으로 나갑니다. 클라이언트는 HTTP 상태가 아니라 `error.code` 를 봅니다.

```json
{
  "success": false,
  "request_id": "9a1c2b3d-...",
  "error": {
    "code": "MEETING_LOCKED",
    "message": "진행 중이거나 종료된 회의는 수정할 수 없습니다.",
    "retryable": false,
    "details": { "status": "ENDED" }
  }
}
```

코드 카탈로그는 `config/errors.py` 한 곳에 있습니다. 정의되지 않은 코드를 쓰면
`BordoError` 생성 시점에 터지므로 오타가 배포까지 가지 않습니다.

### 화살표 진하기의 기준은 회의 종료 시각입니다

`FlowEdge.compute_opacity()` 는 조회 시점이 아니라 **회의 구간**으로 계산합니다.
지금 시각을 기준으로 하면 같은 회의를 내일 열었을 때 그림이 달라집니다.

### 필터는 DB 에서 겁니다

플로우가 차별점인데 필터를 파이썬에서 돌리면 회의가 길어질수록 화면이 느려집니다.
`content_types` · `surfaces` · `since_minutes` 는 전부 쿼리로 내려갑니다.
`participant_ids` 만 JSON 배열이라 DB 별 연산자가 달라 예외로 뒀습니다 —
PostgreSQL 로 확정되면 `contains` 로 내리십시오.

### 카테고리에 맞지 않는 필터는 400 입니다

회의 모드(`MEETING`)에서 `DOCUMENT` 를 요청하면 빈 배열이 아니라 `400` 입니다.
빈 배열로 답하면 "데이터가 없는 것"과 "잘못 물어본 것"을 구분할 수 없습니다.

### 소프트 삭제는 기본 매니저에서 막습니다

`objects` 는 살아 있는 행만, `all_objects` 는 전부 봅니다.
조회할 때마다 `deleted_at IS NULL` 을 손으로 붙이면 언젠가 빠뜨립니다.

### 상태는 PATCH 로 못 바꿉니다

`Meeting.status` 는 전용 엔드포인트로만 움직입니다. 자유롭게 쓸 수 있으면
`PENDING_APPROVAL` 을 건너뛸 수 있어 "사람 최종 승인" 원칙이 무력화됩니다.

### 멱등성

`Idempotency-Key` 를 보내면 같은 키의 재요청에 최초 응답을 그대로 돌려줍니다
(`apps/common/idempotency.py`). 같은 키로 **다른 내용**을 보내면 `409` 입니다 —
클라이언트 버그를 조용히 삼키지 않기 위해서입니다.

---

## 구조

```
config/
  settings.py     환경 · DRF · JWT · BORDO 고유 설정
  errors.py       오류 코드 카탈로그 + BordoError
  exceptions.py   모든 예외를 공통 형식으로
  urls.py         라우팅
apps/
  common/         소프트삭제·멱등성·권한·페이징 (공용)
  accounts/       User · 인증
  orgs/           Team · Project · 즐겨찾기 · 최근항목
  agent/          대리인 설정 · 프롬프트 · 대화 · Run · 유보질문
  meetings/       회의 · 참석자 · 안건 · 플로우 · 요약 · 브리핑
  home/           홈 집계
scripts/
  smoke_test.py   48개 요청 통합 확인
```

---

## 다음에 할 것

우선순위 순입니다.

1. **회의 시작·종료** (`/internal/v1/meetings/start|end`) — Discord Bot 이 부릅니다
2. **Discord 자연어 제어** (`/internal/v1/discord/commands` + 스킬 실행)
3. **WebSocket** — `/ws/projects/{project_id}`. Channels 를 붙이십시오
4. **채팅** — 화면 12장 중 7장이지만 홈·플로우 다음입니다
5. **Celery** — 회의 요약 생성, Outbox 재시도
6. 태스크 승인 · 캘린더 · 문서 (팀 관리 영역, 2차)

### 아직 가정으로 둔 것

| 항목 | 지금 값 | 어디 |
|---|---|---|
| 권한 등급 | OWNER / ADMIN / MEMBER | `apps/orgs/models.py` |
| 소프트 삭제 유예 | 30일 | `settings.BORDO` |
| 페이지 크기 | 50 | `settings.BORDO` |
| 프로젝트 진행률 | 아직 수동. 태스크 붙으면 자동 계산 | `Project.progress` |

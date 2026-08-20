"""
D 담당(채팅 · 현재 상태 · 태스크 · 캘린더 · 문서) 스모크 테스트.

목적은 커버리지가 아니라 **막히는 지점을 찾는 것**입니다. 정상 경로 한 번,
그리고 설계상 막아야 하는 경로를 눌러 실제로 막히는지 봅니다.

    python manage.py migrate
    python manage.py seed_demo
    python manage.py runserver &
    python scripts/smoke_crud.py
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("BORDO_SMOKE_BASE", "http://127.0.0.1:8000") + "/api/v1"
TOKEN = None
FAIL = []
COUNT = 0


def call(method, path, body=None, expect=200, headers=None, label=None):
    global COUNT
    COUNT += 1
    data = json.dumps(body).encode() if body is not None else None
    # 한글 검색어가 쿼리에 들어가므로 URL 인코딩이 필요합니다.
    url = BASE + urllib.parse.quote(path, safe="/?&=")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    try:
        payload = json.loads(raw) if raw else None
    except Exception:
        # 500 이면 Django 가 HTML 을 뱉습니다. 첫 줄만 남겨 원인을 보여줍니다.
        payload = raw.decode("utf-8", "replace")[:200] if raw else None

    tag = label or f"{method} {path}"
    expects = expect if isinstance(expect, (list, tuple)) else [expect]
    ok = code in expects
    if not ok:
        FAIL.append(f"{tag} → {code} (기대 {expects}) "
                    f"{json.dumps(payload, ensure_ascii=False)[:240]}")
    print(f"{'OK  ' if ok else 'FAIL'} {code:3d} {tag}")
    return payload


def section(name):
    print(f"\n─── {name}")


#: 실행마다 새로 만듭니다. 고정값이면 두 번째 실행부터 첫 전송이 중복으로
#: 잡혀, 지난 실행이 남긴 메시지(중요 확인이 이미 끝난)를 돌려받습니다.
CLIENT_MSG_ID = f"cli-{uuid.uuid4().hex[:8]}"

# ═══════════════════════════════════════════ 준비
section("로그인 · 스코프")
login = call("POST", "/auth/login",
             {"email": "susu@bordo.dev", "password": "Bordo!2026"},
             label="로그인")
TOKEN = login["access_token"]
me = call("GET", "/auth/me", label="내 정보")
MY_ID = me["id"]

teams = call("GET", "/teams", label="팀 목록")
TEAM_ID = teams["results"][0]["id"]
projects = call("GET", f"/teams/{TEAM_ID}/projects", label="프로젝트 목록")
# 회의가 들어 있는 프로젝트를 고릅니다 — 플로우·브리핑 검증이 거기 매달려 있습니다.
PRJ = projects["results"][0]["id"]
for p in projects["results"]:
    got = call("GET", f"/projects/{p['id']}/meetings", label=f"회의 확인 · {p['name']}")
    if got.get("results"):
        PRJ = p["id"]
        break
members = call("GET", f"/projects/{PRJ}/members", label="프로젝트 참여자")
OTHER = next((m["user_id"] for m in members["results"] if m["user_id"] != MY_ID), None)

# ═══════════════════════════════════════════ 현재 상태
section("05. 현재 상태 — work · plan · thought")
work = call("POST", f"/projects/{PRJ}/work-items",
            {"title": "team_members 마이그레이션", "category": "database",
             "status": "IN_PROGRESS", "progress": 40, "blockers": ["스키마 확인 대기"]},
            expect=201, label="작업 생성")
WORK_ID = work["id"]
call("GET", f"/projects/{PRJ}/work-items?owner=me", label="내 작업 목록")
call("PATCH", f"/work-items/{WORK_ID}", {"progress": 75}, label="진행률 수정")
call("PATCH", f"/work-items/{WORK_ID}", {"progress": 150}, expect=400,
     label="진행률 150 → 400")
call("GET", f"/work-items/{WORK_ID}", label="작업 상세")

plan_a = call("POST", f"/projects/{PRJ}/plans",
              {"title": "MCP 동기화 엔진", "priority": "P1"}, expect=201,
              label="계획 A 생성")
plan_b = call("POST", f"/projects/{PRJ}/plans",
              {"title": "인덱스 재측정", "priority": "P2",
               "dependencies": [plan_a["id"]]}, expect=201, label="계획 B 생성")
call("PATCH", f"/plans/{plan_a['id']}", {"dependencies": [plan_b["id"]]},
     expect=400, label="순환 의존 A→B→A → 400")
call("PATCH", f"/plans/{plan_a['id']}", {"dependencies": [str(uuid.uuid4())]},
     expect=404, label="없는 선행 계획 → 404")

th = call("POST", f"/projects/{PRJ}/thoughts",
          {"topic": "권한 모델", "content": "team_role 과 project_role 분리 필요",
           "confidence": 0.6, "requires_discussion": True}, expect=201,
          label="생각 기록")
call("PATCH", f"/thoughts/{th['id']}", {"confidence": 1.7}, expect=400,
     label="confidence 1.7 → 400")
priv = call("POST", f"/projects/{PRJ}/thoughts",
            {"topic": "비공개 우려", "content": "혼잣말", "visibility": "private"},
            expect=201, label="비공개 생각")
call("GET", f"/projects/{PRJ}/activity", label="활동 로그")

# ═══════════════════════════════════════════ 태스크
section("07. 태스크 — 상태 전이")
human = call("POST", f"/projects/{PRJ}/tasks",
             {"title": "사람이 만든 태스크", "priority": "P1"}, expect=201,
             label="사람 생성 → TODO")
assert human["status"] == "TODO", human["status"]

ai = call("POST", f"/projects/{PRJ}/tasks",
          {"title": "AI 후보", "created_by_agent": True, "status": "TODO"},
          expect=201, label="AI 생성 (status=TODO 보내도 무시)")
assert ai["status"] == "PENDING_APPROVAL", ai["status"]
AI_ID = ai["id"]

call("PATCH", f"/tasks/{AI_ID}", {"status": "TODO"}, expect=400,
     label="PATCH 로 status 변경 → 400")
call("POST", f"/tasks/{AI_ID}/complete", expect=409,
     label="승인 전 완료 → 409")
call("DELETE", f"/tasks/{AI_ID}", expect=409,
     label="AI 후보 삭제 → 409 (반려를 쓰라고)")
call("POST", f"/tasks/{AI_ID}/reject", {}, expect=400, label="사유 없는 반려 → 400")
call("POST", f"/tasks/{AI_ID}/approve", {"assignee_id": MY_ID}, label="승인")
call("POST", f"/tasks/{AI_ID}/approve", {}, expect=409, label="중복 승인 → 409")
call("POST", f"/tasks/{AI_ID}/start", label="시작")
done = call("POST", f"/tasks/{AI_ID}/complete", {"note": "배포 완료"}, label="완료")
print(f"     프로젝트 진행률 = {done['project_progress']}%")
call("POST", f"/tasks/{human['id']}/assign", {"assignee_id": str(uuid.uuid4())},
     expect=403, label="비참여자에게 배정 → 403")
call("GET", "/me/tasks", label="내 할 일 (팀 가로지름)")

# ═══════════════════════════════════════════ 캘린더
section("08. 캘린더 + 발송함")
ev = call("POST", f"/projects/{PRJ}/calendar/events",
          {"title": "DB 스키마 리뷰", "start_at": "2026-09-07T10:00:00+09:00",
           "end_at": "2026-09-07T11:00:00+09:00", "notify_discord": True},
          expect=201, label="일정 생성 + 공지 요청")
EV = ev["id"]
print(f"     현지 시각 = {json.dumps(ev['local_times'], ensure_ascii=False)[:120]}")
assert ev["discord_notified"] is True, "공지 요청했는데 표시가 안 올랐습니다"
call("POST", f"/calendar/events/{EV}/confirm", label="확정 (+리마인더 +회의 상태)")
call("POST", f"/calendar/events/{EV}/confirm", label="재확정 — 리마인더 중복 없음")
call("POST", f"/calendar/events/{EV}/notify-discord", {"channel": "announcement"},
     expect=202, label="공지 재요청 → 202 (발송은 A 담당)")
call("DELETE", f"/calendar/events/{EV}", expect=409,
     label="공지 나간 일정 삭제 → 409 (취소 먼저)")
call("POST", f"/calendar/events/{EV}/cancel", {"reason": "문서 리뷰로 대체"},
     label="취소")
call("POST", f"/calendar/events/{EV}/cancel", {"reason": "또"}, expect=409,
     label="중복 취소 → 409")
call("GET", f"/projects/{PRJ}/calendar/events?from=2026-09-01&to=2026-09-30",
     label="기간 조회")

# ═══════════════════════════════════════════ 문서
section("04. 문서 — 마스킹 · 버전")
doc = call("POST", f"/projects/{PRJ}/documents",
           {"title": "다중 팀 DB 설계", "category": "database",
            "content": "# 설계\n\nOPENAI_API_KEY=sk-abcdefghijklmnopqrstuv\n"
                       "DATABASE_URL=postgres://u:pw@host/db\n본문입니다."},
           expect=201, label="문서 생성 (비밀키 포함)")
DOC = doc["id"]
print(f"     마스킹 {doc['masked_secrets']}건, 본문에 sk- 남음: "
      f"{'sk-abcdefghijklmnopqrstuv' in doc['content']}")
assert doc["masked_secrets"] >= 2, doc["masked_secrets"]
assert "sk-abcdefghijklmnopqrstuv" not in doc["content"]

call("PATCH", f"/documents/{DOC}", {"title": "다중 팀 DB 설계 v2"},
     headers={"If-Match": "1"}, label="버전 1 기준 수정")
call("PATCH", f"/documents/{DOC}", {"title": "충돌"}, headers={"If-Match": "1"},
     expect=409, label="낡은 If-Match → 409")
vs = call("GET", f"/documents/{DOC}/versions", label="버전 이력")
print(f"     현재 버전 = {vs['current_version']}, 스냅샷 {vs['count']}개")
call("GET", f"/documents/{DOC}/versions/1", label="버전 1 본문")
call("POST", f"/documents/{DOC}/restore", {"version": 1, "reason": "되돌림"},
     label="버전 1 복원 (새 버전 생성)")
call("GET", f"/projects/{PRJ}/documents?q=설계", label="문서 검색")
call("DELETE", f"/documents/{DOC}", label="문서 삭제")
call("GET", f"/documents/{DOC}", expect=404, label="삭제 후 조회 → 404")
call("POST", f"/documents/{DOC}/restore-deleted", label="삭제 복구")

# ═══════════════════════════════════════════ 채팅
section("16. 채팅")
sb = call("GET", "/chat/sidebar", label="사이드바 (단체방 자동 생성)")
print(f"     팀 {len(sb['teams'])}개, 총 미읽음 {sb['total_unread']}")
PROJECT_ROOM = None
for t in sb["teams"]:
    for p in t["projects"]:
        if p["group_chat_room_id"]:
            PROJECT_ROOM = p["group_chat_room_id"]
            break

call("GET", "/chat/candidates", label="대화상대 후보")
if OTHER:
    dm = call("POST", "/chat/rooms",
              {"type": "DIRECT", "member_ids": [OTHER], "team_id": TEAM_ID},
              expect=[200, 201], label="1:1 방 생성")
    again = call("POST", "/chat/rooms",
                 {"type": "DIRECT", "member_ids": [OTHER], "team_id": TEAM_ID},
                 expect=200, label="같은 1:1 재요청 → 기존 방")
    assert dm["id"] == again["id"], "1:1 방이 두 개 생겼습니다"
    ROOM = dm["id"]
else:
    ROOM = PROJECT_ROOM

call("POST", "/chat/rooms", {"type": "DIRECT", "member_ids": [MY_ID]}, expect=400,
     label="자기 자신과 1:1 → 400")

m1 = call("POST", f"/chat/rooms/{ROOM}/messages",
          {"body": "확인 부탁드립니다", "is_important": True,
           "client_message_id": CLIENT_MSG_ID}, expect=201, label="메시지 전송")
dupe = call("POST", f"/chat/rooms/{ROOM}/messages",
            {"body": "확인 부탁드립니다", "client_message_id": CLIENT_MSG_ID},
            expect=200, label="같은 client_message_id 재전송 → 기존 메시지")
assert m1["id"] == dupe["id"], "중복 메시지가 생겼습니다"

call("POST", f"/chat/rooms/{ROOM}/messages", {"body": "", "attachment_ids": []},
     expect=400, label="본문·첨부 모두 빈 전송 → 400")
call("GET", f"/chat/rooms/{ROOM}/messages", label="메시지 목록 (커서)")
call("GET", f"/chat/rooms/{ROOM}/messages?date=2026-08-13&before=x", expect=400,
     label="date + before 동시 → 400")
call("GET", f"/chat/rooms/{ROOM}/messages?date=2026-08-13", label="날짜 선택 조회")

imp = call("GET", "/chat/important", label="중요 채팅 (미확인만)")
print(f"     미확인 중요 {imp['count']}건")
call("POST", f"/chat/messages/{m1['id']}/important/confirm", label="중요 확인")
after = call("GET", "/chat/important", label="확인 후 중요 채팅")
assert after["count"] < imp["count"] or imp["count"] == 0, "확인해도 안 빠집니다"

call("PATCH", f"/chat/messages/{m1['id']}", {"body": "확인 부탁드립니다 (오타 수정)"},
     label="15분 이내 수정")
call("POST", f"/chat/rooms/{ROOM}/read", {"up_to_message_id": m1["id"]},
     label="읽음 처리")
call("GET", f"/chat/rooms/{ROOM}/active-dates?month=2026-08", label="달력 활성 날짜")
call("GET", f"/chat/rooms/{ROOM}/daily-summary", label="날짜별 요약")
call("GET", f"/chat/rooms/{ROOM}/search?q=확인", label="방 안 검색")
call("DELETE", f"/chat/messages/{m1['id']}", label="메시지 삭제 (내용만 비움)")

if PROJECT_ROOM:
    call("PATCH", f"/chat/rooms/{PROJECT_ROOM}", {"title": "결정 모드 라운지"},
         label="단체방 이름 변경")
if OTHER and ROOM != PROJECT_ROOM:
    call("PATCH", f"/chat/rooms/{ROOM}", {"title": "바꾸기"}, expect=409,
         label="1:1 방 이름 변경 → 409")
    call("POST", f"/chat/rooms/{ROOM}/members", {"user_ids": [MY_ID]}, expect=409,
         label="1:1 방에 사람 추가 → 409")

call("GET", f"/chat/rooms/{uuid.uuid4()}", expect=404, label="남의 방 조회 → 404")

# ═══════════════════════════════════════════ 디자인 반영분
section("09/10. 디자인 반영 — 플로우 화살표 · 홈")
meetings = call("GET", f"/projects/{PRJ}/meetings", label="회의 목록")
# 끝난 회의라야 플로우·브리핑이 있습니다. 예정 회의는 아직 그릴 게 없습니다.
ended = [m for m in meetings["results"] if m["status"] == "ENDED"]
MTG = (ended or meetings["results"] or [{}])[0].get("id")

if MTG:
    fl = call("GET", f"/meetings/{MTG}/flow?category=MEETING", label="플로우 (화살표 집계)")
    assert "arrows" in fl, "arrows 가 없습니다 (edges 그대로면 화살표가 겹칩니다)"
    print(f"     노드 {len(fl['nodes'])} · 화살표 {len(fl['arrows'])}")
    for a in fl["arrows"]:
        badge = " ".join(f"{c['label']} {c['count']}" for c in a["counts"])
        print(f"       {a['direction_label'] or a['id']} → {badge}")
    # 한 쌍에 여러 건이 묶였는지 = 집계가 실제로 동작하는지
    assert any(a["total_count"] > 1 for a in fl["arrows"]), "집계가 전부 1건입니다"
    assert any(c["label"] == "일정" for a in fl["arrows"] for c in a["counts"]), \
        "일정(SCHEDULE) 종류가 안 보입니다"
    print(f"     필터 가능 종류 = {fl['filter_options']['content_types']}")

    call("GET", f"/meetings/{MTG}/flow?content_types=REVISION", expect=400,
         label="없어진 REVISION 필터 → 400")
    call("GET", f"/meetings/{MTG}/flow?content_types=SCHEDULE,CONCLUSION",
         label="일정+결론 필터")

    # 브리핑은 **대리 참석한 회의에만** 있습니다. 프로젝트를 고르는 규칙이
    # "회의가 있는 첫 프로젝트" 라, 대리 참석이 없던 회의가 잡히면 404 가 정상입니다 —
    # 그때도 실패로 찍으면 스모크가 늘 빨간 줄을 하나 달고 다니게 됩니다.
    br = call("GET", f"/meetings/{MTG}/ai-briefing", expect=(200, 404),
              label="AI 브리핑 (B 담당 · 조회만)")
    if "used_answers" in br:
        print(f"     활용 {len(br['used_answers'])} · 유보 {len(br['deferred_answers'])}"
              f" · 답변필요 {len(br['needs_answer'])}")
    else:
        print("     이 회의에는 대리 참석자가 없어 브리핑이 없습니다 (정상)")

home = call("GET", "/home", label="홈")
assert "shortcuts" in home, "shortcuts (Zero/Discord 바로가기) 가 없습니다"
assert "user_avatar_url" in home, "사이드바 프로필 아바타 자리가 없습니다"
print(f"     Zero 방 = {home['shortcuts']['agent_room_id']}, "
      f"Discord 연결 = {home['shortcuts']['discord']['connected']}")
if home["today_schedule"]:
    a = home["today_schedule"][0]["action"]
    print(f"     오늘 일정 버튼 = {a['label']} ({a['kind']})")

# 대리인 방 이름은 저장값이 아니라 주인의 지금 호칭입니다. `AI 대리인` 은 화면
# 어디에도 없는 낱말이라, 목록에 그게 남아 있으면 조립 규칙이 안 걸린 것입니다.
sidebar = call("GET", "/chat/sidebar", label="채팅 사이드바")
agent_room = sidebar["my_agent_room"]
assert agent_room and agent_room["title"].endswith("Bordo"),     f"대리인 방 이름이 호칭 규칙을 안 따릅니다: {agent_room and agent_room['title']}"
print(f"     내 대리인 방 = {agent_room['title']}")
if home["recent_meeting_summary"]:
    s = home["recent_meeting_summary"]
    print(f"     최근 회의 = {s['team_name']} / {s['title']} / 불참={s['missed']}")
    # `agent_summary` 가 아니라 `zero_summary` 입니다. 화면에서 대리인의 호칭이
    # `Zero` 라 계약·구현·CLAUDE.md 가 모두 그 이름을 씁니다 — 스크립트만
    # 옛 이름으로 남아 있어 여기서 항상 멈췄습니다.
    assert "main_decisions" in s and "zero_summary" in s, "요약 카드 필드가 옛 모양입니다"

# ═══════════════════════════════════════════ 결과
print(f"\n{'═' * 60}")
if FAIL:
    print(f"실패 {len(FAIL)} / {COUNT}")
    for f in FAIL:
        print(f"  · {f}")
    raise SystemExit(1)
print(f"전부 통과 — {COUNT}개 호출")

import datetime as dt
import json, urllib.parse, urllib.request, urllib.error

import os
BASE = os.environ.get("BORDO_SMOKE_BASE", "http://127.0.0.1:8000") + "/api/v1"
TOKEN = None
FAIL = []

def call(method, path, body=None, expect=200, auth=True, headers=None, label=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth and TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        req.add_header("Idempotency-Key", f"idem-{method}-{path}")
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
        payload = raw[:200]
    tag = label or f"{method} {path}"
    ok = code == expect
    if not ok:
        FAIL.append(f"{tag} → {code} (기대 {expect}) {json.dumps(payload, ensure_ascii=False)[:200]}")
    print(f"{'OK ' if ok else 'FAIL'} {code:3d} {tag}")
    return payload

# 인증
call("POST", "/auth/signup", {"email": "susu@bordo.dev", "password": "x"},
     expect=409, auth=False, label="POST /auth/signup (중복 이메일 → 409)")
login = call("POST", "/auth/login", {"email": "susu@bordo.dev", "password": "Bordo!2026"},
             auth=False)
TOKEN = login["access_token"]
call("POST", "/auth/login", {"email": "susu@bordo.dev", "password": "wrong"},
     expect=401, auth=False, label="POST /auth/login (틀린 비번 → 401)")
call("GET", "/home", expect=401, auth=False, label="GET /home (토큰 없음 → 401)")

# 홈
home = call("GET", "/home")
print(f"     └ 최근회의 {len(home['recent_meetings'])} · 오늘일정 {len(home['today_schedule'])}"
      f" · greeting={home['greeting_mode']} · 불참뱃지={[m['missed'] for m in home['recent_meetings']]}")
call("POST", "/me/briefing-dismiss", {"action": "OPEN", "always_open": True})

# 팀 · 프로젝트
teams = call("GET", "/teams")
team_id = teams["results"][0]["id"]
call("GET", f"/teams/{team_id}")
call("GET", f"/teams/{team_id}/members")
projects = call("GET", f"/teams/{team_id}/projects")
pid = next(p["id"] for p in projects["results"] if p["name"] == "글로벌 회의 도구")
call("GET", f"/projects/{pid}")
call("GET", f"/projects/{pid}/members")
call("PUT", f"/projects/{pid}/favorite")
call("GET", "/me/projects/recent")
call("GET", "/me/projects/favorites")

# 회의 · 플로우
ms = call("GET", f"/projects/{pid}/meetings")
ended = [m for m in ms["results"] if m["status"] == "ENDED"][0]
mid = ended["id"]
call("GET", f"/meetings/{mid}")
fl = call("GET", f"/meetings/{mid}/flow?category=MEETING")
# 화살표는 사람 쌍마다 하나이고 종류별 개수가 뱃지로 붙습니다.
print(f"     └ 노드 {len(fl['nodes'])} · 화살표 {len(fl['arrows'])}"
      f" · opacity={[a['opacity'] for a in fl['arrows']]}")
# 작업 플로우는 **프로젝트·기간** 스코프입니다. 회의에 매달리지 않으므로
# `/meetings/{id}/flow?category=WORK` 로 물으면 한 건도 안 나옵니다. 그 경로로
# 확인하고 있으면 화면이 빈 채로 열려도 여기서는 통과합니다.
frm = urllib.parse.quote(
    (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat())
w = call("GET", f"/projects/{pid}/flow?from={frm}")
print(f"     └ 작업 화살표 {len(w['arrows'])} · 기간 {w.get('period_label')}"
      f" · 종류 {w['filter_options']['content_types']}"
      f" · 출처 {w['filter_options']['sources']}")
# 다섯 칸이 다 있어야 화면의 필터가 온전합니다. 하나라도 비면 그 칸이 안 뜹니다.
assert set(w["filter_options"]["content_types"]) >= {
    "WORK", "REVISION", "FEEDBACK", "SHARE", "AI_LOOKUP"}, \
    w["filter_options"]["content_types"]
assert w["filter_options"]["sources"], "출처 필터가 비었습니다"
# 진하기는 저장값이 아니라 **조회 기간**으로 계산합니다. 전부 같으면
# 계산이 안 걸린 것입니다.
opac = {a["opacity"] for a in w["arrows"]}
assert len(opac) > 1, f"진하기가 균일합니다: {opac}"
call("GET", f"/meetings/{mid}/flow?category=MEETING&content_types=DOCUMENT", expect=400,
     label="GET flow (회의모드에 DOCUMENT → 400)")
f2 = call("GET", f"/meetings/{mid}/flow?category=MEETING&content_types=REQUEST")
print(f"     └ 필터 적용 후 화살표 {len(f2['arrows'])}"
      f" · 합계 {sum(a['total_count'] for a in f2['arrows'])}건")
idx = call("GET", f"/meetings/{mid}/indexes?category=MEETING")
print(f"     └ 인덱스 {idx['count']} · related_edge_ids={[len(r['related_edge_ids']) for r in idx['results']]}")
call("GET", f"/meetings/{mid}/indexes?category=WORK")
st = call("GET", f"/meetings/{mid}/summary-table")
print(f"     └ 발견한문제 {len(st['discovered_issues'])} · 변동 {len(st['changes'])} · 계획 {len(st['next_plans'])}")
call("GET", f"/meetings/{mid}/context")
ag = call("GET", f"/meetings/{mid}/agendas")
# 뱃지를 누르면 그 종류에 묶인 낱개 전달로 내려갑니다.
edge_id = fl["arrows"][0]["counts"][0]["edge_ids"][0]
det = call("GET", f"/flow-edges/{edge_id}")
print(f"     └ 전달 상세: agenda={bool(det['agenda'])} document={bool(det['document'])}")
# 작업 화살표에도 상세가 열려야 합니다. 여긴 회의가 없어 권한 검사가 다른
# 갈래를 타므로(프로젝트 멤버십), 눌러 봐야 확인됩니다.
work_edge = w["arrows"][0]["counts"][0]["edge_ids"][0]
d2 = call("GET", f"/flow-edges/{work_edge}")
print(f"     └ 작업 상세: 종류={d2['edge']['content_type']} 출처={d2['edge']['source'] or '-'}")

# `AI 조회` 는 4단 상세가 따로 있습니다. 엣지에서 그리로 갈 수 있어야 화살표를
# 눌렀을 때 빈 패널이 안 뜹니다.
lookup_edge = next(eid
                   for a in w["arrows"] for c in a["counts"]
                   if c["content_type"] == "AI_LOOKUP" for eid in c["edge_ids"])
d3 = call("GET", f"/flow-edges/{lookup_edge}")
assert d3["lookup_id"], "AI 조회 화살표에서 상세로 갈 길이 없습니다"
lk = call("GET", f"/agent-lookups/{d3['lookup_id']}")
print(f"     └ AI 조회 4단: 이유={bool(lk['reason'])} 질문={bool(lk['question'])}"
      f" 확인={lk['answered']} 출처={lk['source']}")

# AI 브리핑
br = call("GET", f"/meetings/{mid}/ai-briefing")
print(f"     └ 활용 {len(br['used_answers'])} · 유보 {len(br['deferred_answers'])} · 답변필요 {len(br['needs_answer'])}")
call("GET", f"/meetings/{mid}/pending-questions")

# 대리인 설정
call("GET", "/me/agent/settings")
r = call("PATCH", "/me/agent/settings", {"allow_midmeeting_question": True})
print(f"     └ version {r['previous_version']} → {r['settings']['active_version']} · changed={list(r['changed'])}")
r2 = call("PATCH", "/me/agent/settings", {"allow_midmeeting_question": True},
          headers={"Idempotency-Key": "idem-noop"}, label="PATCH settings (변화 없음)")
print(f"     └ 변화 없으면 버전 유지: {r2['settings']['active_version']}")
call("GET", "/me/agent/settings/history")
p = call("POST", "/me/agent/prompts", {"body": "간결하게 답하고 불확실하면 유보한다."}, expect=201)
call("GET", "/me/agent/prompts")
call("PATCH", f"/me/agent/prompts/{p['id']}", {"body": "근거를 붙여서 답한다."})
call("DELETE", f"/me/agent/prompts/{p['id']}", expect=204)

# 대리 참석 · 필터 프리셋
call("POST", f"/meetings/{mid}/delegate", {"enabled": True, "prompt": "일정은 확인 후 결정"})
fp = call("POST", "/me/flow-filters", {"name": "백엔드만", "content_types": ["OPINION"]}, expect=201)
call("GET", "/me/flow-filters")
call("DELETE", f"/me/flow-filters/{fp['id']}", expect=204)

# 권한 · 낙관적 잠금
call("GET", "/meetings/00000000-0000-0000-0000-000000000000", expect=404,
     label="GET /meetings/{없는id} → 404")
call("PATCH", f"/projects/{pid}", {"name": "이름 변경"}, headers={"If-Match": "999"},
     expect=409, label="PATCH project (If-Match 불일치 → 409)")

print("\n" + "="*60)
if FAIL:
    print(f"실패 {len(FAIL)}건")
    for f in FAIL: print("  -", f)
else:
    print("전부 통과")

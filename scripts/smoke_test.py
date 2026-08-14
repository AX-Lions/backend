import json, urllib.request, urllib.error

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
w = call("GET", f"/meetings/{mid}/flow?category=WORK")
print(f"     └ WORK 화살표 {len(w['arrows'])} · 필터옵션 {w['filter_options']['content_types']}")
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
work_edge = w["arrows"][0]["counts"][0]["edge_ids"][0]
d2 = call("GET", f"/flow-edges/{work_edge}")
print(f"     └ 문서 전달: document={bool(d2['document'])} sections={len((d2['document'] or {}).get('sections',[]))}")

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

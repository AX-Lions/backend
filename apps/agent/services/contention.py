"""
예상 논쟁점 — 회의 전에 "여기서 의견이 갈릴 것" 을 미리 짚습니다.

준비 화면(`회의 대리 참석 준비`)의 윗단을 채웁니다. 자리를 비우는 사람이
**무엇에 대해 입장을 정해 둬야 하는지** 를 모르면 준비 화면은 빈 칸입니다.

## 사실은 코드가 모으고, 갈림만 모델이 짚습니다

브리핑(`briefing.py`)과 같은 규칙입니다. 지난 회의의 발언과 작업 변경은 DB 에
있는 사실이라 코드가 그대로 뽑고, 모델은 **그 사실들 사이에서 어디가 갈리는지**
만 판단합니다. 원문을 통째로 주고 "요약해서 쟁점을 만들어" 라고 하면 없던
발언이 근거로 붙습니다.

근거 카드는 **모델이 만들지 않습니다.** 모델은 몇 번 사실을 썼는지만 가리키고,
카드는 코드가 원본에서 다시 만듭니다. 인용문을 모델이 쓰게 두면 조금씩 고쳐
써서, 화면의 따옴표 안 문장이 실제 발언과 달라집니다.

## 모델이 없어도 화면은 섭니다

`OPENAI_API_KEY` 가 없거나 호출이 실패하면 **안건에서 규칙으로** 만듭니다.
개발 환경에서 준비 화면이 통째로 비면 붙였는지 안 붙였는지 알 수 없습니다.

## ReAct 를 쓰지 않는 이유

`react.run` 의 `search_meeting` 은 `ctx.meeting_id` 한 회의로 고정돼 있습니다.
아직 시작도 안 한 회의를 스코프로 주면 발언이 0건이라 근거가 언제나 비고,
`judge` 의 근거없음 판정으로 전부 유보됩니다. 여기서 보는 것은 **이 회의가
아니라 그 앞의 회의들**이라 조회 경로부터 다릅니다.
"""
from __future__ import annotations

import hashlib
import json
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("bordo.agent")

#: 몇 개를 예상하는가. 화면이 아코디언 세 칸으로 그려져 있습니다.
TARGET_COUNT = 3

#: 얼마나 거슬러 보는가. 지난 회의 발언과 작업 변경 모두에 적용됩니다.
LOOKBACK_DAYS = 14

#: 모델에게 넘길 사실의 상한. 전부 실으면 규칙보다 길어져 모델이 앞을 흘립니다.
MAX_FACTS = 24

_SYSTEM = """\
회의 전에 **의견이 갈릴 지점**을 짚습니다.

아래는 지난 회의 발언과 작업 변경 기록입니다. 번호가 붙어 있습니다.

## 지켜야 할 것

1. 아래 사실에서 읽히는 것만 쓰십시오. 없는 내용을 지어내지 마십시오.
2. 각 쟁점은 **둘 중 하나를 골라야 하는 질문** 이어야 합니다.
   ("A 할 것인가, B 할 것인가?" 형태)
3. 한쪽이 명백히 옳은 것은 쟁점이 아닙니다. 실제로 갈릴 만한 것만 고르십시오.
4. `evidence` 에는 근거가 된 사실의 **번호만** 적으십시오. 문장을 옮겨 적지 마십시오.
5. 근거가 없으면 그 쟁점을 빼십시오. 개수를 채우려고 만들지 마십시오.

## 출력

JSON 만 출력하십시오. 다른 말은 붙이지 마십시오.

{"points": [
  {"title": "질문형 한 줄",
   "options": [{"title": "짧은 이름", "description": "한 줄 설명"},
               {"title": "짧은 이름", "description": "한 줄 설명"}],
   "rationale": "왜 이게 갈릴 것으로 보는지 2~3문장",
   "evidence": [1, 3]}
]}
"""


# ═══════════════════════════════════════════ 사실 모으기


def _meeting_facts(meeting, tz, since) -> list[dict]:
    """
    같은 프로젝트의 **지난 회의** 발언.

    이 회의가 아닙니다 — 아직 열리지도 않았습니다. 앞선 회의에서 무슨 말이
    오갔는지가 이번에 무엇이 갈릴지의 근거입니다.
    """
    from apps.common.display import date_label
    from apps.meetings.models import Meeting, MeetingStatus, Utterance

    prev = (Meeting.objects
            .filter(project_id=meeting.project_id, status=MeetingStatus.ENDED,
                    scheduled_at__gte=since)
            .exclude(pk=meeting.pk)
            .order_by("-scheduled_at")[:5])
    if not prev:
        return []

    rows = (Utterance.objects
            .filter(meeting__in=list(prev))
            .select_related("meeting")
            .order_by("-spoken_at", "-id")[:MAX_FACTS])

    # **갈 곳이 있는 회의에만 링크를 답니다.**
    #
    # 전에는 근거 카드마다 무조건 `회의에서 보기` 가 붙었습니다. 그런데 눌러서
    # 여는 곳은 그 회의의 플로우 판이고, 화살표가 하나도 없는 회의는 빈 판만
    # 뜹니다 — 카드마다 단추가 있는데 절반은 아무것도 없는 데로 갑니다.
    #
    # 한 번에 모읍니다. 발언마다 확인하면 근거 20건에 조회가 20번 더 나갑니다.
    from apps.meetings.models import FlowEdge
    with_flow = set(FlowEdge.objects
                    .filter(meeting__in=list(prev))
                    .values_list("meeting_id", flat=True))

    out = []
    for u in rows:
        body = (u.body or "").strip()
        if len(body) < 10:
            # `넵`, `ㅇㅋ` 같은 맞장구는 쟁점의 근거가 되지 않습니다.
            continue
        at = u.spoken_at or u.meeting.scheduled_at
        out.append({
            "kind": "meeting",
            "title": f"{date_label(u.meeting.scheduled_at, tz)} · {u.meeting.title}",
            "who": u.participant_name or "",
            "at": at,
            "body": body[:300],
            "link": ({"label": "회의에서 보기", "meeting_id": str(u.meeting_id)}
                     if u.meeting_id in with_flow else None),
        })
    return out


def _work_facts(meeting, tz, since) -> list[dict]:
    """
    최근 **작업 변경**.

    진행률이 오른 것보다 일정이 밀리거나 막힌 것이 쟁점이 됩니다. 그래서
    바뀐 필드를 그대로 문장으로 옮기고, 무엇이 바뀌었는지를 화면이 보여줍니다.
    """
    from apps.common.display import date_label
    from apps.states.models import ActivityEvent, Visibility, WorkItem

    rows = (ActivityEvent.objects
            .filter(project_id=meeting.project_id, occurred_at__gte=since,
                    kind__startswith="work.")
            .exclude(kind="work.deleted")
            .select_related("actor")
            .order_by("-occurred_at")[:MAX_FACTS])

    # **비공개 작업은 근거가 되면 안 됩니다.**
    #
    # 활동 로그에는 공개 여부가 없습니다. 여기서 안 거르면 본인이 비공개로 둔
    # 작업의 제목과 변경 내용이 근거 카드에 실려 **회의 참석자 전원**에게
    # 보입니다 — 대리인 발언은 `can_disclose` 가 거르지만 이 화면은 그 앞입니다.
    #
    # 이미 지워진 작업도 뺍니다. 지워진 것을 근거로 "이렇게 갈릴 것" 이라고
    # 말하면 사용자는 화면에서 찾을 수 없는 것을 놓고 입장을 정하게 됩니다.
    target_ids = [e.target_id for e in rows]
    open_ids = set(WorkItem.objects
                   .filter(id__in=target_ids, visibility=Visibility.TEAM)
                   .values_list("id", flat=True))

    out = []
    for e in rows:
        if e.target_id not in open_ids:
            continue
        line = _describe_change(e.detail or {}, e.kind)
        if not line:
            continue
        who = e.actor.display_name if e.actor_id else "(탈퇴한 사용자)"
        out.append({
            "kind": "work",
            "title": f"{date_label(e.occurred_at, tz)} · 작업 변경사항",
            "who": f"{who}의 작업",
            "at": e.occurred_at,
            "body": line,
            # **작업에는 링크를 안 답니다.** 작업 기록 하나를 여는 화면이 없어
            # 눌러도 아무 데도 못 갑니다. 「현재 상태」 화면이 생기면 여기에
            # 다시 답니다.
            "link": None,
        })
    return out


#: 화면에 그대로 찍히는 필드 이름.
_FIELD_LABEL = {
    "status": "상태", "progress": "진행률", "title": "제목",
    "expected_end_at": "완료 예정일", "planned_end_at": "완료 예정일",
    "planned_start_at": "시작 예정일", "blockers": "막힌 이유",
    "priority": "우선순위", "summary": "요약",
}


def _describe_change(detail: dict, kind: str = "") -> str:
    """
    `{"expected_end_at": {"from": ..., "to": ...}}` → `완료 예정일이 A → B 로 바뀌었어요`.

    바뀐 값을 문장으로 만드는 것을 클라이언트에 맡기지 않습니다. 근거 카드는
    화면 세 곳(준비 화면·브리핑·플로우)에 같은 모양으로 나가야 합니다.

    `kind` 를 함께 받는 이유 — 생성·삭제 로그의 `detail` 모양이 `{"title": ...}`
    로 **똑같습니다.** 로그 종류를 안 보면 지워진 작업이 `새로 생겼어요` 로 찍힙니다.
    """
    if "title" in detail and not isinstance(detail.get("title"), dict):
        if kind and not kind.endswith(".created"):
            return ""
        return f"「{detail['title']}」 작업이 새로 생겼어요."

    parts = []
    for field, change in detail.items():
        if not isinstance(change, dict) or "to" not in change:
            continue
        label = _FIELD_LABEL.get(field)
        if not label:
            continue
        before, after = _short(change.get("from")), _short(change.get("to"))
        parts.append(f"{label}이(가) {before} → {after} 로 바뀌었어요."
                     if before else f"{label}이(가) {after} 로 정해졌어요.")
    return " ".join(parts[:2])


def _short(value) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value[:2])
    text = str(value)
    return text[:40]


def gather_facts(meeting) -> list[dict]:
    """
    쟁점 판단의 재료. **최신 것이 앞**입니다.

    회의 발언과 작업 변경을 시각으로 섞습니다. 종류별로 나눠 주면 모델이
    앞쪽 종류에서만 근거를 고르는 쏠림이 생깁니다.
    """
    from apps.common.display import user_tz

    tz = user_tz(meeting.created_by)
    since = timezone.now() - timezone.timedelta(days=LOOKBACK_DAYS)
    facts = _meeting_facts(meeting, tz, since) + _work_facts(meeting, tz, since)
    facts.sort(key=lambda f: f["at"], reverse=True)
    return facts[:MAX_FACTS]


# ═══════════════════════════════════════════ 쟁점 만들기


def _fact_lines(facts: list[dict]) -> str:
    lines = []
    for i, f in enumerate(facts, start=1):
        head = "회의 발언" if f["kind"] == "meeting" else "작업 변경"
        who = f" · {f['who']}" if f["who"] else ""
        lines.append(f"{i}. [{head}{who}] {f['title']}\n   {f['body']}")
    return "\n".join(lines)


def _parse(raw: str) -> list[dict]:
    """
    모델이 돌려준 JSON.

    코드 펜스를 붙여 오는 일이 실제로 있어 벗겨 냅니다. 깨져 있으면 빈 목록을
    돌려주고 호출부가 규칙 기반으로 떨어집니다 — 여기서 예외를 던지면 준비
    화면이 통째로 500 이 됩니다.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, IndexError):
        logger.warning("논쟁점 JSON 파싱 실패: %.200s", raw)
        return []
    points = data.get("points") if isinstance(data, dict) else data
    return points if isinstance(points, list) else []


def _option_rows(raw) -> list[dict]:
    """선택지에 `A` · `B` 를 코드가 붙입니다. 모델이 붙이면 건너뛰거나 겹칩니다."""
    out = []
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        # 걸러진 것을 건너뛰고 **남은 것에 차례로** 붙입니다. 원본 인덱스를 쓰면
        # 앞이 걸러졌을 때 키가 `B` 부터 시작해 화면에 A 없는 선택지가 뜹니다.
        out.append({"key": chr(ord("A") + len(out)), "title": title[:60],
                    "description": str(item.get("description") or "").strip()[:120]})
    return out[:4]


def _evidence_rows(picked, facts: list[dict]) -> list[dict]:
    """
    번호 → 근거 카드.

    **원본에서 다시 만듭니다.** 모델이 인용문을 쓰게 두면 조금씩 고쳐 써서
    화면의 따옴표 안 문장이 실제 발언과 달라집니다.
    """
    out = []
    for n in (picked if isinstance(picked, list) else []):
        try:
            idx = int(n)
        except (ValueError, TypeError):
            continue
        # 번호는 1부터입니다. 0 이나 음수를 그대로 빼면 파이썬 음수 인덱스가 되어
        # **맨 뒤 사실**이 엉뚱하게 근거로 붙습니다.
        if idx < 1 or idx > len(facts):
            continue
        fact = facts[idx - 1]
        out.append({k: fact[k] for k in ("kind", "title", "who", "body", "link")}
                   | {"at": fact["at"].isoformat()})
    return out[:4]


def _source_key(title: str) -> str:
    """
    재예측 때 같은 쟁점을 알아보는 열쇠.

    제목에서 만듭니다. 순서로 만들면 예측이 다시 돌 때 2번이 다른 쟁점이
    되면서 **1번에 적어 둔 입장이 엉뚱한 쟁점에 붙습니다.**
    """
    norm = " ".join((title or "").split()).lower()
    return "t:" + hashlib.sha256(norm.encode()).hexdigest()[:24]


def _from_llm(meeting, facts, client) -> list[dict]:
    if not facts:
        return []
    r = client.chat([{"role": "user",
                      "content": f"회의 제목: {meeting.title}\n\n{_fact_lines(facts)}"}],
                    system=_SYSTEM)
    if not r.ok:
        logger.warning("논쟁점 생성 실패: %.200s", r.error)
        return []

    out = []
    for item in _parse(r.text)[:TARGET_COUNT]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        options = _option_rows(item.get("options"))
        if not title or len(options) < 2:
            # 선택지가 하나면 고를 것이 없습니다. 쟁점이 아닙니다.
            continue
        out.append({
            "title": title[:200],
            "options": options,
            "rationale": str(item.get("rationale") or "").strip()[:800],
            "evidence": _evidence_rows(item.get("evidence"), facts),
            "created_by_agent": True,
        })
    return out


def _from_agendas(meeting) -> list[dict]:
    """
    모델이 없을 때. **안건을 그대로 질문으로 돌립니다.**

    예측이라고 부르기엔 모자라지만, 준비 화면이 빈 칸으로 서는 것보다는
    "이 안건에 대해 입장을 적어 두라" 가 낫습니다. `created_by_agent=False` 로
    남겨 화면이 `Bordo가 예상했어요` 문구를 붙이지 않게 합니다.
    """
    from apps.meetings.models import Agenda

    out = []
    for a in Agenda.objects.filter(meeting=meeting).order_by("sort_order")[:TARGET_COUNT]:
        out.append({
            "title": f"{a.title} — 어떻게 할 것인가?",
            "options": [{"key": "A", "title": "찬성", "description": ""},
                        {"key": "B", "title": "반대", "description": ""}],
            "rationale": "회의 안건입니다. 미리 입장을 적어 두면 대리인이 그대로 전합니다.",
            "evidence": [],
            "created_by_agent": False,
        })
    return out


# ═══════════════════════════════════════════ 저장


def build_for(meeting, client=None) -> int:
    """
    이 회의의 예상 논쟁점을 만들어 저장하고 **개수**를 돌려줍니다.

    ## 이미 있는 것을 지우지 않습니다

    `source_key` 로 갱신합니다. 통째로 지우고 새로 만들면 사람이 이미 적어 둔
    입장이 함께 사라집니다 — 준비 화면을 다시 채우게 하는 것은 예측을 한 번 더
    돌린 값보다 비쌉니다.

    이번에 다시 나오지 않은 쟁점 중 **아무도 답하지 않은 것만** 지웁니다.
    답이 달린 것은 남깁니다. 사람이 시간을 들인 자리라, 예측이 마음을 바꿨다고
    치울 것이 아닙니다.
    """
    from apps.meetings.models import DebatePoint

    from .llm import client as default_client
    client = client or default_client

    # **모델 호출을 트랜잭션 밖에서 합니다.** 안에 두면 응답을 기다리는 수십 초
    # 동안 DB 트랜잭션이 열려 있어, 그 사이 같은 행을 건드리는 요청이 밀립니다.
    rows = _from_llm(meeting, gather_facts(meeting), client) or _from_agendas(meeting)
    if not rows:
        # 아무것도 못 만들었으면 **있던 것을 건드리지 않습니다.** 모델이 한 번
        # 실패했다고 이미 보여 주던 쟁점이 사라지면, 화면을 열어 둔 사람은
        # 방금까지 있던 목록이 빈 칸이 된 것을 봅니다.
        return 0

    with transaction.atomic():
        seen = []
        for row in rows:
            key = _source_key(row["title"])
            if key in seen:
                continue
            seen.append(key)
            DebatePoint.objects.update_or_create(
                meeting=meeting, source_key=key,
                defaults={"order": len(seen), "title": row["title"],
                          "options": row["options"], "rationale": row["rationale"],
                          "evidence": row["evidence"],
                          "created_by_agent": row["created_by_agent"]})

        (DebatePoint.objects
         .filter(meeting=meeting, stances__isnull=True)
         .exclude(source_key__in=seen)
         .delete())

        # 답이 달려 살아남은 쟁점은 새 목록 **뒤에** 붙습니다. 번호를 다시 매기지
        # 않으면 `논쟁점 01` 이 둘이 되어 화면에서 어느 것을 말하는지 알 수 없습니다.
        for order, row in enumerate(
                DebatePoint.objects.filter(meeting=meeting)
                .exclude(source_key__in=seen).order_by("created_at"),
                start=len(seen) + 1):
            DebatePoint.objects.filter(pk=row.pk).update(order=order)
    return len(seen)

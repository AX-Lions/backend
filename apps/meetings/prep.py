"""
회의 대리 참석 준비 — 불참 등록과 준비 화면.

자리를 비우기로 한 사람이 회의 **전에** 채워 두는 자리입니다.

    홈 「오늘 일정」의 `회의에 참여하지 않아요`  ─┐
                                                 ├→ POST /meetings/{id}/absence
    Discord 회의 시작 팝업의 `불참하기`          ─┘        → 준비 화면(GET .../prep)

`views.py` 와 파일을 나눈 이유 — 저기는 이미 800줄에 회의 CRUD·플로우·브리핑이
얹혀 있습니다. 준비 화면은 모델 셋(참석자·논쟁점·입장)과 대리인 설정을 함께
읽는 별개의 묶음이라, 같은 파일에 두면 무엇이 무엇의 도우미인지 흐려집니다.

## 왜 `delegate` 와 따로 두는가

`POST /meetings/{id}/delegate` 는 **전량 교체** 계약입니다 — `prompt` 키가 없으면
빈 문자열로 덮어씁니다. 홈의 대리 참석 팝업이 이미 그 계약으로 붙어 있습니다.

준비 화면에는 `적용하기` 가 여럿이라(현재 설정 사용 · 이번에만 다르게 · 추가 설정)
한 버튼이 다른 버튼의 입력을 지우면 안 됩니다. 그래서 여기는 **보낸 것만 바꾸는**
부분 갱신으로 두고, `delegated` 플래그의 단일 진입점은 `absence` 하나로 모읍니다.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.agent.tasks import build_debate_points
from apps.common.display import meeting_when, user_tz
from apps.common.events import publish
from apps.common.permissions import meeting_access
from config.errors import BordoError

from .models import (Attendance, DebatePoint, DebateStance, MeetingParticipant,
                     MeetingStatus)

#: 논쟁점 상태. 화면 뱃지와 1:1 입니다.
#
# `답변중...` 은 여기 없습니다 — 그건 지금 펼쳐 놓고 타이핑 중이라는 뜻이라
# 서버가 알 수 없고, 저장되면 곧바로 `답변완료` 입니다. 서버가 흉내 내려면
# 초안 저장을 따로 받아야 하는데 화면에 그런 버튼이 없습니다.
STATUS_LABEL = {"ANSWERED": "답변완료", "NEEDED": "답변필요"}

#: 회의별 프롬프트 개수 상한. 평소 지시(`prompts._STANDING_MAX`)와 맞춥니다.
PROMPT_MAX = 5

#: 입장 한 건의 길이 상한. 프롬프트에 그대로 실리는 값이라 열어 두면
#: 한 사람이 적어 둔 글이 규칙보다 길어집니다.
STANCE_MAX = 2000

#: 이번 회의에만 덮어쓸 수 있는 설정.
#
# 화면의 세부 설정 토글 6개와 1:1 입니다. `agent_name` · `active_version` 은
# 넣지 않습니다 — 회의 하나 때문에 대리인 호칭이 바뀌면 다른 회의의 플로우
# 노드와 이름이 갈리고, 버전은 평소 설정의 이력이라 회의가 건드릴 것이 아닙니다.
OVERRIDE_BOOLS = ("mention_feasibility", "allow_schedule_change",
                  "allow_midmeeting_question",
                  "disclose_work", "disclose_plan", "disclose_thought")

#: 응답에는 실리지만 이 회의에서 바꿀 수 없는 것. 되돌려받아도 조용히 버립니다.
#:
#: 정말 모르는 키는 그대로 400 입니다 — 오타를 조용히 버리면 화면은 저장됐다고
#: 하는데 대리인은 그 설정을 안 봅니다.
READ_ONLY_KEYS = ("disclose_work_plan_thought", "agent_name", "active_version",
                  "updated_at")

#: 불참을 등록할 수 있는 회의 상태.
#
# 끝난 회의에 대리 참석을 켤 수 있으면 브리핑이 이미 만들어진 뒤에 참석자 상태가
# 바뀝니다 — 화면은 `Bordo 대리 참석 예정` 이라고 하는데 회의는 어제 끝난 것입니다.
OPEN_STATUSES = (MeetingStatus.DRAFT, MeetingStatus.SCHEDULED,
                 MeetingStatus.CONFIRMED, MeetingStatus.ACTIVE)

logger = logging.getLogger("bordo.meetings")


def participant_of(user, meeting_id):
    """
    `(meeting, participant)`.

    참석자가 아니면 404 입니다 — 프로젝트 참여자이긴 하나 이 회의에 초대되지
    않은 사람에게 403 을 주면 "그 회의에 내가 없다" 가 아니라 "권한이 없다" 로
    읽혀, 초대해 달라고 해야 할 사람이 관리자에게 권한을 달라고 합니다.
    """
    meeting = meeting_access(user, meeting_id)
    p = (MeetingParticipant.objects
         .filter(meeting=meeting, user=user)
         # 헤더가 팀 이름까지 씁니다. 조인을 미리 걸어 두지 않으면 응답 하나에
         # 프로젝트·팀 조회가 두 번 더 나갑니다.
         .select_related("meeting", "meeting__project", "meeting__project__team")
         .first())
    if p is None:
        raise BordoError("STATE_NOT_FOUND",
                         "이 회의의 참석자가 아닙니다. 회의를 만든 사람에게 "
                         "참석자로 추가해 달라고 하십시오.",
                         details={"meeting_id": str(meeting_id)})
    return meeting, p


def require_open(meeting):
    if meeting.status not in OPEN_STATUSES:
        raise BordoError("MEETING_LOCKED",
                         "이미 끝난 회의에는 불참을 등록할 수 없습니다.",
                         details={"status": meeting.status})


def register_absence(user, meeting_id):
    """
    불참 등록 = **대리 참석 켜기**.

    화면 문구는 `회의에 참여하지 않아요` 지만 뱃지는 `Bordo 대리 참석 예정` 입니다.
    자리를 비우는 것과 대리인을 보내는 것이 이 서비스에서는 같은 행위입니다 —
    `delegated=False` 로 두면 회의 후 브리핑이 통째로 안 생기고(`briefing.build_all`
    이 대리 참석자만 돕니다) 사용자는 없는 사이 무슨 일이 있었는지 못 봅니다.

    **`delegate_prompt` 와 `allowed_sources` 는 건드리지 않습니다.** 껐다 켰다 할 때
    준비 화면에서 적어 둔 것이 사라지면 안 됩니다.
    """
    meeting, p = participant_of(user, meeting_id)
    require_open(meeting)

    changed = not p.delegated or p.attendance != Attendance.DELEGATED
    if changed:
        p.delegated = True
        p.attendance = Attendance.DELEGATED
        p.save(update_fields=["delegated", "attendance", "updated_at"])
        publish(meeting.project_id, "meeting.absence.registered",
                {"meeting_id": str(meeting.id), "user_id": str(user.id)})

    # 준비 화면이 열리자마자 쟁점을 보여줘야 합니다. 화면에서 따로 부르게 하면
    # 열 때마다 모델이 돌고, 두 번째 사람이 열었을 때 예측이 달라집니다.
    #
    # **기다리지 않습니다.** 여기서 터지면 등록 자체가 실패한 것으로 읽힙니다 —
    # 불참은 이미 저장돼 있는데 화면은 안 눌린 줄 압니다.
    try:
        build_debate_points.delay(str(meeting.id))
    except Exception:                                          # noqa: BLE001
        logger.exception("논쟁점 생성 호출 실패 meeting=%s", meeting.id)

    return meeting, p, changed


def cancel_absence(user, meeting_id):
    """
    참여로 되돌립니다.

    진행 중인 회의면 `PRESENT`, 아직 안 열렸으면 `PENDING` 입니다. 열리지도 않은
    회의를 `PRESENT` 로 두면 참석자 목록에 이미 와 있는 사람으로 그려집니다.

    적어 둔 입장과 설정은 지우지 않습니다. 마음을 바꿔 다시 불참을 누르는 일이
    실제로 있고, 그때 다시 쓰게 하면 화면을 두 번 채우게 됩니다.
    """
    meeting, p = participant_of(user, meeting_id)
    # 끝난 회의도 막습니다. 지난 회의의 대리 참석 기록을 지우면 홈 카드가
    # `불참한 회의` 에서 `참석한 회의` 로 뒤집히고, 이미 만들어진 브리핑은
    # 대리 참석을 전제로 쓰여 있어 화면과 내용이 어긋납니다.
    require_open(meeting)
    p.delegated = False
    p.attendance = (Attendance.PRESENT if meeting.status == MeetingStatus.ACTIVE
                    else Attendance.PENDING)
    p.save(update_fields=["delegated", "attendance", "updated_at"])
    publish(meeting.project_id, "meeting.absence.cancelled",
            {"meeting_id": str(meeting.id), "user_id": str(user.id)})
    return meeting, p


def header_of(meeting, participant, user) -> dict:
    """
    화면 헤더.

    문자열을 서버가 완성해 내려줍니다. 브라우저 시간대로 찍으면 시간대가 다른
    팀원이 같은 회의를 다른 시각으로 봅니다 — 이 서비스의 전제가 그것입니다.
    """
    from apps.agent.services.flow import agent_display_name

    tz = user_tz(user)
    ends_at = meeting.scheduled_at + timezone.timedelta(minutes=meeting.duration_min)
    delegated = bool(participant and participant.delegated)
    return {
        "meeting_id": str(meeting.id),
        "title": meeting.title,
        "project_name": meeting.project_name,
        "team_name": meeting.project.team.name if meeting.project_id else "",
        "scheduled_at": meeting.scheduled_at,
        "ends_at": ends_at,
        # `8월 18일 14:00 - 15:00`
        "when": meeting_when(meeting.scheduled_at, ends_at, tz),
        "location": "Discord" if meeting.discord_channel_id else "서비스",
        "status": meeting.status,
        "delegated": delegated,
        # 뱃지 문구. 클라이언트가 필드 조합으로 만들면 `대리 참석 예정` 과
        # `대리 참석 중` 이 화면마다 갈립니다.
        "badge": (f"{agent_display_name(user)} 대리 참석 "
                  + ("중" if meeting.status == MeetingStatus.ACTIVE else "예정")
                  ) if delegated else "참석 예정",
    }


def point_row(point, stance) -> dict:
    """논쟁점 하나 + 내 입장."""
    status = "ANSWERED" if stance else "NEEDED"
    return {
        "id": str(point.id),
        "order": point.order,
        # `논쟁점 01`. 두 자리 맞춤을 클라이언트가 하면 10번째에서 갈립니다.
        "label": f"논쟁점 {point.order:02d}",
        "title": point.title,
        "options": point.options or [],
        "rationale": point.rationale,
        "evidence": point.evidence or [],
        "created_by_agent": point.created_by_agent,
        "status": status,
        "status_label": STATUS_LABEL[status],
        "stance": ({
            "id": str(stance.id),
            "option_key": stance.option_key or None,
            "body": stance.body,
            "updated_at": stance.updated_at,
        } if stance else None),
    }


def debate_notice(rows, delegated: bool) -> str:
    """
    화면 부제.

    세 가지를 가릅니다. 하나로 뭉치면 **거짓말이 됩니다** —
    안건을 베낀 것에 `Bordo가 예상했어요` 를 붙이거나, 만들고 있는 중에
    `없어요` 라고 단정하게 됩니다.
    """
    if rows:
        if any(r["created_by_agent"] for r in rows):
            return (f"Bordo가 회의 자료와 이전 논의를 바탕으로 {len(rows)}개의 "
                    f"논쟁점을 예상했어요.")
        # 규칙으로 안건을 옮겨 온 것입니다. 예측이라고 말하면 안 됩니다.
        return f"회의 안건 {len(rows)}개예요. 미리 입장을 적어 두면 대리인이 그대로 전합니다."
    if delegated:
        # 불참을 등록하면 곧바로 예측이 걸립니다. 아직 없다고 단정하면 사용자는
        # 새로고침할 이유를 못 찾습니다.
        return "Bordo가 논쟁점을 예상하고 있어요. 잠시 후 다시 확인해 주세요."
    return "아직 예상된 논쟁점이 없어요. 회의 자료가 쌓이면 Bordo가 예상해 드려요."


def debate_block(meeting, user, delegated: bool = False) -> dict:
    """
    예상 논쟁점과 내 입장.

    입장을 논쟁점마다 따로 조회하지 않습니다. 세 개짜리 목록에 쿼리가 세 번 더
    나가는 것을 막는 것이 아니라, 재예측으로 개수가 늘면 그만큼 늘기 때문입니다.
    """
    points = list(DebatePoint.objects.filter(meeting=meeting))
    mine = {str(s.point_id): s
            for s in DebateStance.objects.filter(point__in=points, user=user)}
    rows = [point_row(p, mine.get(str(p.id))) for p in points]
    answered = sum(1 for r in rows if r["status"] == "ANSWERED")
    return {
        # 화면 부제. 개수와 출처가 들어가므로 서버가 완성합니다.
        "notice": debate_notice(rows, delegated),
        "count": len(rows),
        "answered_count": answered,
        "points": rows,
    }


def setup_block(participant, user) -> dict:
    """
    「Bordo 활동 설정」.

    **실제로 적용될 값과 평소 값을 함께 내려줍니다.** 화면에 `현재 설정 사용` 과
    `이번에만 다르게 사용` 이 나란히 있어, 되돌렸을 때 무엇으로 돌아가는지
    보여주려면 둘 다 있어야 합니다.
    """
    from apps.agent.models import AgentPrompt, AgentSettings
    from apps.agent.services import policy
    from apps.agent.services.prompts import standing_prompts_for

    # 설정 화면에 한 번도 안 들어간 사람은 행이 없습니다. 빈 객체를 내려주면
    # **화면 토글이 전부 꺼진 것처럼 그려지는데 대리인은 기본값(대부분 켜짐)으로
    # 판정합니다.** 사용자가 보는 것과 실제가 정반대가 됩니다.
    row = AgentSettings.objects.filter(user=user).first()
    standing = row.as_snapshot() if row else dict(policy.DEFAULTS)
    effective = participant.effective_settings(standing)

    cards = list(AgentPrompt.objects.filter(user=user)
                 .values("id", "body", "created_at"))
    standing_bodies = standing_prompts_for(user)
    override = participant.prompt_override
    return {
        "mode": "STANDING" if participant.uses_standing_settings else "ONCE",
        "mode_label": ("현재 설정 사용" if participant.uses_standing_settings
                       else "이번에만 다르게 사용"),
        # 이번 회의에서 실제로 판정에 쓰일 값.
        "settings": effective,
        # 평소 설정. `현재 설정 사용` 을 눌렀을 때 돌아갈 자리입니다.
        "standing_settings": standing,
        "overridden_keys": sorted(participant.settings_override or {}),
        "prompts": (list(override) if override is not None else standing_bodies),
        "standing_prompts": [{"id": str(c["id"]), "body": c["body"]} for c in cards],
        # 평소 지시는 최근 다섯 개만 실립니다. 화면이 이 수를 모르면 여섯 번째
        # 카드가 조용히 무시되고 사용자는 적어 둔 것이 지켜진다고 믿습니다.
        "standing_prompt_limit": len(standing_bodies),
        # 「추가 설정」 자유 입력. 평소 지시보다 뒤에 실려 우선합니다.
        "extra_note": participant.delegate_prompt,
        # `null` 이면 고른 적 없음(제한 없음), `[]` 면 아무것도 안 봄.
        "sources": participant.allowed_sources,
    }


# ═══════════════════════════════════════════ 엔드포인트


def clean_overrides(raw) -> dict:
    """
    보낸 것만 남깁니다. 모르는 키는 **400** 입니다.

    조용히 버리면 화면은 저장됐다고 하는데 대리인은 그 설정을 안 봅니다.
    사용자가 껐다고 믿는 것이 계속 나가는 쪽이 훨씬 나쁩니다.
    """
    from apps.agent.models import AgentTone

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BordoError("VALIDATION_ERROR", "settings 는 객체여야 합니다.")

    out, bad = {}, []
    for key, value in raw.items():
        if key in READ_ONLY_KEYS:
            # `GET /prep` 이 준 `settings` 를 그대로 돌려보내는 것이 정상입니다.
            # 화면이 그중 바꿀 수 있는 것만 골라 담게 하면, 어느 키가 읽기
            # 전용인지 클라이언트가 알고 있어야 합니다. 여기서 버립니다.
            continue
        if key in OVERRIDE_BOOLS:
            out[key] = bool(value)
        elif key == "tone":
            tone = str(value or "").upper()
            if tone not in AgentTone.values:
                raise BordoError("VALIDATION_ERROR", "고를 수 없는 말투입니다.",
                                 details={"tone": sorted(AgentTone.values)})
            out[key] = tone
        else:
            bad.append(key)
    if bad:
        raise BordoError("VALIDATION_ERROR", "이 회의에서 바꿀 수 없는 설정입니다.",
                         details={"unknown": bad,
                                  "allowed": sorted(OVERRIDE_BOOLS) + ["tone"]})
    return out


def clean_prompts(raw) -> list[str]:
    """`null` 은 호출부가 걸러 냅니다. 여기 오는 것은 배열뿐입니다."""
    if not isinstance(raw, list):
        raise BordoError("VALIDATION_ERROR",
                         "prompts 는 배열입니다. 평소 것을 그대로 쓰려면 null 을 보내십시오.")
    out = []
    for item in raw:
        body = str(item or "").strip()
        if body:
            out.append(body[:2000])
    # 개수 상한. 평소 지시는 `_STANDING_MAX`(5개)로 잘리는데 회의별 목록에만
    # 상한이 없으면, 여기 스무 개를 넣은 사람의 프롬프트가 **매 실행마다**
    # 통째로 실려 규칙보다 길어집니다 — 모델이 앞쪽 규칙을 흘립니다.
    return out[:PROMPT_MAX]


def load_point(user, point_id):
    """`(point, participant)`. 회의 참석자만 입장을 적을 수 있습니다."""
    point = (DebatePoint.objects.filter(pk=point_id)
             .select_related("meeting").first())
    if point is None:
        raise BordoError("DEBATE_POINT_NOT_FOUND",
                         details={"debate_point_id": str(point_id)})
    _, p = participant_of(user, point.meeting_id)
    return point, p


@api_view(["PUT", "DELETE"])
def stance(request, point_id):
    """
    논쟁점에 대한 나의 입장 — 저장(덮어쓰기) · 삭제.

    `PUT` 인 이유는 **사람마다 하나**이기 때문입니다. 같은 쟁점에 내 입장이 둘이면
    대리인이 어느 쪽으로 말할지 정할 수 없습니다. 두 번 보내면 갱신입니다.
    """
    point, participant = load_point(request.user, point_id)

    if request.method == "DELETE":
        deleted, _ = DebateStance.objects.filter(point=point, user=request.user).delete()
        if not deleted:
            raise BordoError("STATE_NOT_FOUND", "아직 적어 둔 입장이 없습니다.")
        return Response(status=204)

    require_open(point.meeting)

    body = str(request.data.get("body") or "").strip()
    if not body:
        raise BordoError("VALIDATION_ERROR",
                         "입장을 적어 주십시오. 비워 두려면 삭제하십시오.")
    if len(body) > STANCE_MAX:
        raise BordoError("VALIDATION_ERROR",
                         f"입장은 {STANCE_MAX}자 이내로 적어 주십시오. "
                         "대리인 프롬프트에 그대로 실리는 값입니다.",
                         details={"length": len(body), "max": STANCE_MAX})

    option_key = str(request.data.get("option_key") or "").strip()
    if option_key:
        keys = [str(o.get("key") or "") for o in (point.options or [])]
        if option_key not in keys:
            # 조용히 버리면 사용자는 A 를 골랐는데 대리인은 아무것도 안 고른 채
            # 말합니다. 무엇을 고를 수 있었는지까지 돌려줍니다.
            raise BordoError("VALIDATION_ERROR", "그 논쟁점에 없는 선택지입니다.",
                             details={"option_key": option_key, "allowed": keys})

    row, created = DebateStance.objects.update_or_create(
        point=point, user=request.user,
        defaults={"body": body, "option_key": option_key})

    publish(point.meeting.project_id, "meeting.debate.stance.saved",
            {"meeting_id": str(point.meeting_id), "debate_point_id": str(point.id),
             "user_id": str(request.user.id)})
    return Response(point_row(point, row), status=201 if created else 200)


@api_view(["PUT"])
def agent_setup(request, meeting_id):
    """
    「Bordo 활동 설정」 저장.

    **보낸 것만 바꿉니다.** 화면에 `적용하기` 가 셋(현재 설정 사용 · 이번에만
    다르게 · 추가 설정)이라 전량 교체로 두면 한 버튼이 다른 버튼의 입력을 지웁니다.

        mode: "STANDING"   덮어쓴 것을 전부 지우고 평소 설정으로 돌아간다
        mode: "ONCE"       settings·prompts 를 이번 회의에만 적용한다
        (mode 없음)        보낸 키만 바꾼다
    """
    meeting, p = participant_of(request.user, meeting_id)
    require_open(meeting)

    fields = []
    mode = str(request.data.get("mode") or "").upper()
    if mode and mode not in ("STANDING", "ONCE"):
        raise BordoError("VALIDATION_ERROR", "mode 는 STANDING 또는 ONCE 입니다.",
                         details={"mode": mode})

    if mode == "STANDING":
        # `현재 설정 사용` — 되돌리기입니다. 함께 온 settings 는 무시합니다.
        p.settings_override = {}
        p.prompt_override = None
        fields += ["settings_override", "prompt_override"]
    else:
        if "settings" in request.data:
            p.settings_override = clean_overrides(request.data["settings"])
            fields.append("settings_override")
        if "prompts" in request.data:
            raw = request.data["prompts"]
            # `null` 은 "평소 것 그대로", `[]` 는 "아무 프롬프트도 안 씀" 입니다.
            # 둘을 같게 다루면 전부 지운 사람의 대리인이 평소 지시를 그대로 씁니다.
            p.prompt_override = None if raw is None else clean_prompts(raw)
            fields.append("prompt_override")

    if "extra_note" in request.data:
        p.delegate_prompt = str(request.data["extra_note"] or "").strip()[:2000]
        fields.append("delegate_prompt")

    if "sources" in request.data:
        from .views import _clean_sources
        p.allowed_sources = _clean_sources(request.data["sources"])
        fields.append("allowed_sources")

    if fields:
        p.save(update_fields=fields + ["updated_at"])
        publish(meeting.project_id, "meeting.agent_setup.saved",
                {"meeting_id": str(meeting.id), "user_id": str(request.user.id),
                 "changed": fields})
    return Response(setup_block(p, request.user))


@api_view(["GET"])
def prep(request, meeting_id):
    """
    준비 화면 한 번에.

    헤더·논쟁점·입장·설정을 한 응답으로 내려줍니다. 화면이 네 번 부르면 그 사이
    불참을 취소한 경우 헤더는 `참석 예정` 인데 아래는 대리인 설정을 그리고 있는
    상태가 나옵니다.
    """
    meeting, p = participant_of(request.user, meeting_id)
    return Response({
        "header": header_of(meeting, p, request.user),
        "debate": debate_block(meeting, request.user, delegated=p.delegated),
        "agent_setup": setup_block(p, request.user),
    })


@api_view(["POST", "DELETE"])
def absence(request, meeting_id):
    """
    `POST` 불참 등록 · `DELETE` 참여로 되돌리기.

    본문이 필요 없습니다. 홈의 버튼 하나로 눌리고, 세부 설정은 준비 화면에서
    따로 저장합니다 — 여기서 함께 받으면 버튼 하나가 준비 화면의 입력을 덮습니다.
    """
    if request.method == "DELETE":
        meeting, p = cancel_absence(request.user, meeting_id)
        body = header_of(meeting, p, request.user)
        body["prep_url"] = None
        return Response(body)

    meeting, p, changed = register_absence(request.user, meeting_id)
    body = header_of(meeting, p, request.user)
    # 화면이 바로 이어서 열 곳. 클라이언트가 경로를 조립하면 경로가 바뀔 때
    # 두 곳을 고쳐야 합니다.
    body["prep_url"] = f"/api/v1/meetings/{meeting.id}/prep"
    body["created"] = changed
    return Response(body, status=201 if changed else 200)

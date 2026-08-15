"""
`/internal/v1` — Discord 봇 전용.

## 이 진입점의 성격

봇은 **판단하지 않고 릴레이합니다.** 그래서 여기 뷰들은 받은 것을 그대로 저장하고,
해석은 대리인(`apps/agent`)에게 넘깁니다.

## 원본을 지우지 않습니다

회의 발언은 `Utterance` 로 **원문 그대로** 쌓입니다. 요약은 백엔드가 종료 시점에
따로 만듭니다.

봇이 요약해서 보내면 그 과정에서 빠진 내용이 영영 사라집니다. 요약에서 생략된
한 줄이 누군가에게는 중요할 수 있고, 나중에 대리인이 사람마다 다르게 정리해
주려면 원본이 있어야 합니다. (2026-08-15 회의 결정)

## 멱등

같은 발언이 두 번 들어와도 한 번만 처리합니다. 봇이 재시도하거나 Discord 가
같은 이벤트를 두 번 보내는 일이 실제로 있습니다.
"""
from __future__ import annotations

import logging
import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.common.parsing import parse_dt
from config.errors import BordoError

from .auth import service_token_required
from .models import GuildLink, LinkCode

logger = logging.getLogger("bordo.discord")

def internal(methods):
    """`@api_view` + 인증 해제 + 서비스 토큰 검사를 한 번에."""
    def deco(fn):
        fn = service_token_required(fn)
        fn = permission_classes([])(fn)
        fn = authentication_classes([])(fn)
        fn = api_view(methods)(fn)
        return fn
    return deco


def _require(value, field: str) -> str:
    """
    빈 값을 조회에 넣지 않습니다.

    미연동 사용자는 `discord_user_id=""` 로 저장되고, 웹에서 만든 회의는
    `discord_channel_id=""` 입니다. 빈 문자열로 조회하면 **"못 찾음" 이 아니라
    아무 행이나 잡힙니다** — 남의 대리인이 답하거나 남의 회의가 종료됩니다.

    호출부마다 가드를 두는 대신 여기서 막습니다. 조회를 하나 더 추가할 때
    가드를 빠뜨리는 일이 없어야 합니다.
    """
    v = str(value or "").strip()
    if not v:
        raise BordoError("VALIDATION_ERROR", f"{field} 는 필수입니다.")
    return v


def _team_of(guild_id: str):
    guild_id = _require(guild_id, "guild_id")
    link = GuildLink.objects.filter(guild_id=guild_id).select_related("team").first()
    if link is None:
        raise BordoError("TEAM_NOT_FOUND",
                         "이 Discord 서버에 연결된 팀이 없습니다. /bordo-link-team 으로 먼저 연결하십시오.")
    return link.team


def _user_of(discord_user_id: str):
    from apps.accounts.models import User
    discord_user_id = _require(discord_user_id, "discord_user_id")
    user = User.objects.filter(discord_user_id=discord_user_id).first()
    if user is None:
        raise BordoError("USER_NOT_FOUND",
                         "연결된 계정이 없습니다. /bordo-connect 로 먼저 연결하십시오.")
    return user


# ═══════════════════════════════════════════ 계정 · 팀

@internal(["POST"])
def connect_code(request):
    """
    계정 연결 코드를 발급합니다. 봇이 DM 으로 전달합니다.

    **코드는 서버가 만듭니다.** 봇이 만들면 서버가 그 값을 믿을 근거가 없습니다.
    """
    discord_user_id = _require(request.data.get("discord_user_id"),
                               "discord_user_id")

    # 이전에 발급한 살아 있는 코드는 무효로 만듭니다. 여러 개가 동시에 살아 있으면
    # 어느 것이 최신인지 사용자가 알 수 없습니다.
    LinkCode.objects.filter(discord_user_id=discord_user_id,
                            used_at=None).update(used_at=timezone.now())

    # 6자리라 드물게 겹칩니다. 그때 500 을 내면 사용자는 이유를 알 수 없습니다.
    row = None
    for _ in range(5):
        try:
            row = LinkCode.objects.create(
                code=secrets.token_hex(3).upper(),
                discord_user_id=discord_user_id,
                guild_id=str(request.data.get("guild_id") or ""),
                expires_at=timezone.now() + timezone.timedelta(
                    minutes=LinkCode.TTL_MINUTES),
            )
            break
        except IntegrityError:
            continue
    if row is None:
        raise BordoError("INTERNAL_ERROR",
                         "코드 발급에 실패했습니다. 다시 시도해 주십시오.")
    return Response({"code": row.code,
                     "expires_at": row.expires_at.isoformat(),
                     "ttl_minutes": LinkCode.TTL_MINUTES}, status=201)


@internal(["GET"])
def teams_current(request):
    """이 Discord 서버가 어느 팀에 묶여 있는지."""
    guild_id = str(request.query_params.get("guild_id") or "").strip()
    discord_user_id = str(request.query_params.get("discord_user_id") or "").strip()

    if guild_id:
        team = _team_of(guild_id)
        return Response({"team_id": str(team.id), "name": team.name,
                         "linked": True})

    # guild_id 가 없으면 사용자 기준으로 답합니다. 봇이 DM 에서 부르는 경우입니다.
    if not discord_user_id:
        raise BordoError("VALIDATION_ERROR", "guild_id 또는 discord_user_id 가 필요합니다.")

    from apps.orgs.models import TeamMember
    user = _user_of(discord_user_id)
    rows = (TeamMember.objects.filter(user=user).select_related("team")
            .order_by("joined_at"))
    return Response({"linked": bool(rows),
                     "teams": [{"team_id": str(m.team_id), "name": m.team.name,
                                "role": m.team_role} for m in rows]})


@internal(["POST"])
def teams_link(request):
    """
    이 Discord 서버를 팀에 연결합니다.

    ## 왜 연결 코드를 따로 두지 않는가

    계정 연결에는 코드가 필요했습니다. Discord 계정과 Bordo 계정이 **서로를 모르니**
    둘을 잇는 증거가 있어야 했습니다.

    팀 연결에는 필요 없습니다. 이미 이어진 계정으로 부르므로 서버가 그 사람이 팀
    OWNER 인지 압니다. Discord 서버 권한은 봇이 슬래시 명령에서 확인합니다.
    **두 증거가 이미 확보돼 있어 코드를 한 번 더 주고받을 이유가 없습니다.**

    ## 봇이 반드시 해야 하는 것

    `manage_guild` 권한 확인입니다. 백엔드는 Discord 권한을 알 수 없어, 이걸 봇이
    안 하면 **아무나 이 서버를 남의 팀에 붙일 수 있습니다.**
    """
    from apps.orgs.models import TeamMember, TeamRole

    guild_id = _require(request.data.get("guild_id"), "guild_id")
    user = _user_of(request.data.get("discord_user_id"))

    # 팀을 만들거나 관리할 수 있는 사람만 연결합니다. 일반 멤버가 붙이면
    # 팀 전체의 회의가 그 서버로 흘러갑니다.
    memberships = list(TeamMember.objects
                       .filter(user=user, team_role__in=[TeamRole.OWNER, TeamRole.ADMIN])
                       .select_related("team"))
    if not memberships:
        raise BordoError("TEAM_ACCESS_DENIED",
                         "팀의 소유자 또는 관리자만 서버를 연결할 수 있습니다.")

    team_id = str(request.data.get("team_id") or "").strip()
    if team_id:
        chosen = next((m for m in memberships if str(m.team_id) == team_id), None)
        if chosen is None:
            raise BordoError("TEAM_ACCESS_DENIED", "그 팀의 소유자 또는 관리자가 아닙니다.")
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        # 어느 팀인지 서버가 정하면 안 됩니다. 잘못 고르면 남의 팀 회의가
        # 이 서버로 흘러가고, 되돌려도 그 사이 오간 발언은 남습니다.
        raise BordoError(
            "TEAM_AMBIGUOUS", "연결할 팀을 골라 주십시오.",
            details={"teams": [{"team_id": str(m.team_id), "name": m.team.name}
                               for m in memberships]})

    link, created = GuildLink.objects.update_or_create(
        guild_id=guild_id,
        defaults=dict(team=chosen.team, linked_by=user))

    return Response({"team_id": str(link.team_id), "name": chosen.team.name,
                     "created": created}, status=201 if created else 200)


# ═══════════════════════════════════════════ 대리 참석

def _set_delegate(request, on: bool):
    from apps.meetings.models import Attendance, MeetingParticipant, MeetingStatus

    user = _user_of(request.data.get("discord_user_id"))

    # 진행 중이거나 예정된 회의에만 적용합니다. 끝난 회의의 참석 상태를 뒤늦게
    # 바꾸면 그때 대리인이 왜 답했는지 기록과 어긋납니다.
    qs = MeetingParticipant.objects.filter(
        user=user,
        meeting__status__in=[MeetingStatus.SCHEDULED, MeetingStatus.ACTIVE])

    changed = qs.update(delegated=on,
                        attendance=Attendance.ABSENT if on else Attendance.PRESENT)
    return Response({"delegated": on, "meetings_updated": changed})


@internal(["POST"])
def delegate_on(request):
    return _set_delegate(request, True)


@internal(["POST"])
def delegate_off(request):
    return _set_delegate(request, False)


# ═══════════════════════════════════════════ 회의

@internal(["POST"])
def meeting_start(request):
    """
    회의를 시작합니다.

    봇이 스레드를 만든 뒤 부릅니다. `thread_id` 를 채널로 잡는 이유는 발언이
    스레드 안에서 오가기 때문입니다.
    """
    from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                      MeetingStatus)

    team = _team_of(request.data.get("guild_id"))

    thread_id = _require(request.data.get("thread_id")
                         or request.data.get("text_channel_id"), "thread_id")

    # 같은 스레드로 두 번 들어오면 기존 회의를 돌려줍니다. 봇이 재시도할 때
    # 회의가 두 개 생기면 발언이 갈라져 어느 쪽도 온전하지 않습니다.
    existing = Meeting.objects.filter(discord_channel_id=thread_id).first()
    if existing:
        return Response({"meeting_id": str(existing.id),
                         "project_id": str(existing.project_id),
                         "duplicate": True}, status=200)

    project = team.projects.order_by("created_at").first()
    if project is None:
        raise BordoError("PROJECT_NOT_FOUND", "팀에 프로젝트가 없습니다. 웹에서 먼저 만드십시오.")

    # 회의를 만든 사람이 필요합니다. 봇은 사용자가 아니므로 팀을 만든 사람으로
    # 둡니다 — 슬래시 명령을 실행한 사람을 넣으면 계정이 안 이어진 경우 막힙니다.
    creator = team.created_by

    title = (request.data.get("agenda") or request.data.get("title")
             or "Discord 회의")

    with transaction.atomic():
        meeting = Meeting.objects.create(
            project=project, project_name=project.name, title=str(title)[:200],
            status=MeetingStatus.ACTIVE, scheduled_at=timezone.now(),
            started_at=timezone.now(), discord_channel_id=thread_id,
            created_by=creator,
        )
        from apps.accounts.models import User

        rows = request.data.get("participants") or []
        # 빈 id 는 걸러냅니다. 그대로 조회하면 미연동 사용자 아무나 잡혀
        # 남의 회의에 등록됩니다.
        by_discord = {str(r.get("discord_user_id") or "").strip(): r
                      for r in rows if str(r.get("discord_user_id") or "").strip()}

        # 참석자마다 쿼리를 날리지 않습니다. 인원이 늘면 그대로 늘어납니다.
        # 계정을 아직 연결하지 않은 참석자는 여기서 빠집니다 — 회의 자체는
        # 진행돼야 하고, 그 사람은 나중에 연결하면 됩니다.
        for u in User.objects.filter(discord_user_id__in=by_discord.keys()):
            delegated = str(
                by_discord[u.discord_user_id].get("status") or "") == "delegated"
            MeetingParticipant.objects.update_or_create(
                meeting=meeting, user=u,
                defaults=dict(user_name=u.name, delegated=delegated,
                              attendance=(Attendance.ABSENT if delegated
                                          else Attendance.PRESENT)))

    return Response({"meeting_id": str(meeting.id),
                     "project_id": str(project.id)}, status=201)


@internal(["POST"])
def meeting_end(request):
    """
    회의를 끝내고 **백엔드가 요약을 만듭니다.**

    봇은 원본만 보냅니다. 봇이 요약하면 그 과정에서 빠진 내용이 영영 사라지고,
    나중에 대리인이 사람마다 다르게 정리해 주지 못합니다.
    """
    from apps.agent.services import briefing
    from apps.meetings.models import Meeting, MeetingStatus

    # 빈 thread_id 로 조회하면 웹에서 만든 회의(discord_channel_id="")가 잡혀
    # 엉뚱한 팀의 회의가 강제 종료됩니다.
    thread_id = _require(request.data.get("thread_id"), "thread_id")
    meeting = Meeting.objects.filter(discord_channel_id=thread_id).first()
    if meeting is None:
        raise BordoError("MEETING_NOT_FOUND", "해당 스레드의 회의를 찾을 수 없습니다.")

    if meeting.status == MeetingStatus.ENDED:
        return Response({"meeting_id": str(meeting.id), "duplicate": True})

    ended_at = parse_dt(request.data.get("ended_at"), "ended_at") or timezone.now()
    meeting.status = MeetingStatus.ENDED
    meeting.ended_at = ended_at
    meeting.save(update_fields=["status", "ended_at", "updated_at"])

    # 요약과 브리핑은 여기서 만듭니다. 실패해도 회의 종료 자체는 되돌리지
    # 않습니다 — 종료가 안 되면 봇이 계속 발언을 넘깁니다.
    briefings = 0
    try:
        briefings = briefing.build_all(meeting)
    except Exception:                                          # noqa: BLE001
        logger.exception("회의 요약 생성 실패 meeting=%s", meeting.id)

    summary = getattr(meeting, "summary", None)
    return Response({
        "meeting_id": str(meeting.id),
        "briefings": briefings,
        "summary": {
            "one_line": getattr(summary, "one_line", ""),
            "discovered_issues": getattr(summary, "discovered_issues", []),
            "changes": getattr(summary, "changes", []),
            "next_plans": getattr(summary, "next_plans", []),
        } if summary else None,
    })


# ═══════════════════════════════════════════ 발언 · 상태

@internal(["POST"])
def discord_messages(request):
    """
    회의 발언을 그대로 저장하고 대리인을 깨웁니다.

    **원문을 그대로 둡니다.** 요약·해석은 뒤에서 합니다.
    """
    from apps.agent.tasks import run_agent_for_utterance
    from apps.meetings.models import Meeting, MeetingStatus, Utterance

    # 위와 같은 이유입니다. 빈 값이면 웹 회의 스레드로 발언이 새어 들어갑니다.
    thread_id = _require(request.data.get("thread_id"), "thread_id")
    body = (request.data.get("content") or request.data.get("body") or "").strip()
    if not body:
        return Response({"skipped": "empty"}, status=202)

    meeting = Meeting.objects.filter(discord_channel_id=thread_id,
                                     status=MeetingStatus.ACTIVE).first()
    if meeting is None:
        # 회의 밖 잡담입니다. 저장하지 않습니다 — 회의록이 아닌 것이 섞이면
        # 대리인이 엉뚱한 맥락을 근거로 삼습니다.
        return Response({"skipped": "no_active_meeting"}, status=202)

    from apps.accounts.models import User
    speaker = User.objects.filter(
        discord_user_id=str(request.data.get("author_discord_id") or "")).first()

    utterance = Utterance.objects.create(
        meeting=meeting, participant=speaker,
        participant_name=(request.data.get("author")
                          or getattr(speaker, "name", "") or "알 수 없음")[:100],
        body=body,
        spoken_at=parse_dt(request.data.get("created_at"), "created_at"),
    )

    # 대리인은 비동기로 깨웁니다. 봇은 기다리지 않습니다.
    try:
        run_agent_for_utterance.delay(str(utterance.id))
    except Exception:                                          # noqa: BLE001
        # 브로커가 없어도 발언은 이미 저장됐습니다. 회의록이 남는 것이 먼저입니다.
        logger.exception("대리인 기동 실패 utterance=%s", utterance.id)

    return Response({"utterance_id": str(utterance.id)}, status=201)


@internal(["POST"])
def discord_presence(request):
    """온라인 여부. 대리 참석 판단의 보조 신호입니다."""
    from apps.meetings.models import Attendance, MeetingParticipant, MeetingStatus

    discord_user_id = _require(request.data.get("discord_user_id"), "discord_user_id")
    status = str(request.data.get("status") or "").lower()

    from apps.accounts.models import User
    user = User.objects.filter(discord_user_id=discord_user_id).first()
    if user is None:
        return Response({"skipped": "unlinked"}, status=202)

    # idle·dnd 는 자리에 있는 상태입니다. 비운 것은 offline·invisible 뿐입니다.
    # online 만 재실로 보면 잠깐 자리를 비운 표시만으로 결석 처리됩니다.
    present = status in ("online", "idle", "dnd")

    # 대리 참석을 켜 둔 사람은 건드리지 않습니다. 본인이 명시적으로 정한 것을
    # 접속 상태 같은 약한 신호로 뒤집으면 안 됩니다.
    changed = (MeetingParticipant.objects
               .filter(user=user, delegated=False,
                       meeting__status=MeetingStatus.ACTIVE)
               .update(attendance=(Attendance.PRESENT if present
                                   else Attendance.ABSENT)))
    return Response({"updated": changed})


# ═══════════════════════════════════════════ 대리인에게 질문

@internal(["POST"])
def deputy_ask(request):
    """
    `/ask-bordo` — 특정 사람의 대리인에게 묻습니다.

    회의 발언과 달리 **대상이 이미 정해져 있습니다.** 대상 판정을 건너뛰고 바로
    대리인을 부릅니다.
    """
    from apps.agent.services import react
    from apps.meetings.models import Meeting, MeetingStatus

    question = (request.data.get("question") or "").strip()
    if not question:
        raise BordoError("VALIDATION_ERROR", "question 은 필수입니다.")

    # 봇이 쓰는 이름과 명세의 이름이 다릅니다. 뜻이 같아 백엔드가 둘 다 받습니다 —
    # 봇을 고치는 동안 호출이 통째로 실패하는 것보다 낫습니다.
    target = _user_of(request.data.get("target_discord_id")
                      or request.data.get("target") or "")

    asker_id = (request.data.get("asker_discord_id")
                or request.data.get("requester_discord_id") or "")
    asker = None
    if asker_id:
        from apps.accounts.models import User
        asker = User.objects.filter(discord_user_id=str(asker_id)).first()

    # thread_id 는 선택입니다(DM 에서도 물을 수 있음). 다만 빈 값으로 조회하면
    # 웹에서 만든 회의(discord_channel_id="")가 잡혀 엉뚱한 회의에 묶입니다.
    thread_id = str(request.data.get("thread_id") or "").strip()
    meeting = (Meeting.objects.filter(discord_channel_id=thread_id,
                                      status=MeetingStatus.ACTIVE).first()
               if thread_id else None)

    outcome = react.run(
        principal=target, question=question, meeting=meeting,
        actor_id=getattr(asker, "id", None), asker=asker,
        project_id=getattr(meeting, "project_id", None),
    )
    return Response({
        "run_id": str(outcome.run.id),
        "answered": outcome.answered,
        "reason": outcome.reason,
        "body": outcome.text,
    }, status=200 if outcome.answered else 202)

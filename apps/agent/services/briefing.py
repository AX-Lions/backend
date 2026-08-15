"""
회의 종료 브리핑.

**"내가 없는 동안 무슨 일이 있었지?"** 의 답이 여기서 만들어집니다.

두 가지를 남깁니다.

    AiBriefing      불참자 개인에게 — 내 대리인이 무엇을 답하고 무엇을 유보했나
    MeetingSummary  회의 전체에 대해 — 발견한 문제 · 변동사항 · 이후 계획

## 사실은 코드가 모으고, 문장만 LLM 이 씁니다

무엇을 답했고 무엇을 유보했는지는 `AgentRun` 에 이미 있습니다. 그걸 모델에게 다시
읽혀 "요약해" 라고 하면 없던 내용이 섞입니다. 목록은 코드가 만들고, 모델은 그
목록을 **읽기 좋은 한 문단으로 옮기는 일만** 합니다.

LLM 이 실패해도 브리핑은 남습니다 — 문장이 비어도 목록은 그대로입니다.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger("bordo.agent")

_NARRATIVE_SYSTEM = """\
회의에 참석하지 못한 사람에게 보여줄 한 문단을 씁니다.

- 아래 목록에 있는 내용만 쓰십시오. 없는 사실을 덧붙이지 마십시오.
- 대리인이 답한 것과 보류한 것을 모두 언급하십시오.
- 3~4문장, 한국어, 담담하게. 인사말은 넣지 마십시오.
"""


def _answers_of(runs) -> tuple[list[dict], list[dict]]:
    """
    실행 기록에서 답변·유보 목록을 뽑습니다.

    `result` 가 비어 있으면 유보입니다 — 루프가 유보를 `COMPLETED` 로 닫되
    본문에는 사유 문구를 넣기 때문에, 사유 코드가 있는 실행을 유보로 봅니다.
    """
    used, deferred = [], []
    for run in runs:
        # 판정 단계에서 남긴 사유를 되읽습니다. steps 는 완료 후 불변이라
        # 이 값이 나중에 바뀌지 않습니다.
        verdict = next((s for s in reversed(run.steps or [])
                        if s.get("kind") == "verdict"), None)
        policy_step = next((s for s in reversed(run.steps or [])
                            if s.get("kind") == "policy"), None)

        answered = bool(verdict and verdict.get("answer"))
        reason = (verdict or {}).get("reason") or (policy_step or {}).get("reason") or ""

        item = {
            "run_id": str(run.id),
            "body": run.result or "",
            "evidence": [e.get("title_snapshot", "") for e in (run.evidence or [])[:3]],
        }
        if answered:
            used.append(item)
        else:
            deferred.append({**item, "reason": reason})
    return used, deferred


def _narrative(used: list[dict], deferred: list[dict], client=None) -> str:
    if not used and not deferred:
        return ""

    from .llm import client as default_client
    client = client or default_client

    lines = []
    for a in used:
        lines.append(f"- 답변함: {a['body'][:200]}")
    for d in deferred:
        lines.append(f"- 보류함({d['reason']}): {d['body'][:200]}")

    r = client.chat([{"role": "user", "content": "\n".join(lines)}],
                    system=_NARRATIVE_SYSTEM)
    if not r.ok:
        # 문장이 없어도 목록은 남습니다. 브리핑 전체가 사라지는 것보다 낫습니다.
        logger.warning("브리핑 문장 생성 실패: %.200s", r.error)
        return ""
    return (r.text or "").strip()


@transaction.atomic
def build_for_user(meeting, user, client=None):
    """
    한 사람의 브리핑을 만듭니다.

    이미 있으면 덮어씁니다 — 회의가 다시 종료 처리되는 경우가 있고, 그때 두 개가
    쌓이면 화면에 같은 브리핑이 두 번 뜹니다.
    """
    from apps.agent.models import AgentRun, AgentSettings
    from apps.meetings.models import AiBriefing

    runs = list(AgentRun.objects.filter(meeting=meeting, user=user)
                .order_by("created_at"))
    if not runs:
        return None

    used, deferred = _answers_of(runs)
    version = (AgentSettings.objects.filter(user=user)
               .values_list("active_version", flat=True).first() or 1)

    briefing, _ = AiBriefing.objects.update_or_create(
        meeting=meeting, user=user,
        defaults=dict(narrative=_narrative(used, deferred, client),
                      used_answers=used, deferred_answers=deferred,
                      settings_version=version),
    )
    return briefing


def build_summary(meeting, client=None):
    """
    회의 요약 세 폴더를 채웁니다.

    발견한 문제 · 변동사항 · 이후 계획. 화면이 이 이름 그대로 씁니다.
    """
    from apps.meetings.models import MeetingSummary, Utterance

    lines = list(Utterance.objects.filter(meeting=meeting)
                 .order_by("id").values_list("participant_name", "body")[:200])
    if not lines:
        return None

    from .llm import client as default_client
    client = client or default_client

    body = "\n".join(f"{who}: {what}" for who, what in lines)
    r = client.chat(
        [{"role": "user", "content": body}],
        system=("회의 발언을 읽고 아래 세 갈래로 나눠 JSON 으로만 답하십시오.\n"
                '{"discovered_issues": [], "changes": [], "next_plans": [],'
                ' "one_line": ""}\n'
                "각 항목은 한 줄짜리 한국어 문장입니다. "
                "발언에 없는 내용을 만들지 마십시오. 해당 없으면 빈 배열로 두십시오."),
    )
    if not r.ok:
        return None

    import json
    text = (r.text or "").strip()
    # 모델이 ```json 으로 감싸는 일이 잦습니다. 그대로 파싱하면 실패합니다.
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("회의 요약 파싱 실패: %.200s", text)
        return None

    summary, _ = MeetingSummary.objects.update_or_create(
        meeting=meeting,
        defaults=dict(
            discovered_issues=data.get("discovered_issues") or [],
            changes=data.get("changes") or [],
            next_plans=data.get("next_plans") or [],
            one_line=(data.get("one_line") or "")[:300],
        ),
    )
    return summary


def build_all(meeting, client=None) -> int:
    """
    회의가 끝났을 때 한 번 부릅니다.

    대리 참석을 켰던 사람 전원에게 브리핑을 만듭니다. 한 사람이 실패해도 나머지는
    만듭니다 — 브리핑 하나 때문에 회의 전체 결과가 사라지면 안 됩니다.
    """
    from apps.meetings.models import MeetingParticipant

    build_summary(meeting, client)

    made = 0
    rows = MeetingParticipant.objects.filter(
        meeting=meeting, delegated=True).select_related("user")
    for p in rows:
        try:
            if build_for_user(meeting, p.user, client):
                made += 1
        except Exception:                                      # noqa: BLE001
            logger.exception("브리핑 생성 실패 meeting=%s user=%s",
                             meeting.id, p.user_id)
    return made

"""
회의록 조회.

`search_records` 가 **내 기록**을 찾는다면 이것은 **회의에서 오간 말**을 찾습니다.
"아까 누가 뭐라고 했지", "이 안건 결론이 뭐였지" 에 답할 때 씁니다.

회의록은 공개 여부 판정 대상이 아닙니다 — 참석자가 이미 함께 들은 내용입니다.
그래서 `visibility` 를 `team` 으로 고정해 내보냅니다.
"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from .base import SkillBase, SkillContext, SkillKind, SkillResult

LIMIT = 10


def _staleness(dt) -> int:
    if not dt:
        return 0
    return max(0, (timezone.now() - dt).days)


def _excerpt(text: str, limit: int = 240) -> str:
    return (text or "").strip().replace("\n", " ")[:limit]


class SearchMeetingSkill(SkillBase):
    name = "search_meeting"
    kind = SkillKind.READ
    description = (
        "회의에서 오간 발언·안건·요약을 찾습니다. "
        "'아까 무슨 얘기가 나왔나', '이 안건 결론이 뭔가' 에 답할 때 씁니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색어. 비우면 최근 발언을 봅니다."},
            "scope": {
                "type": "string",
                "enum": ["utterance", "agenda", "summary", "all"],
                "description": "생략하면 전부",
            },
        },
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        if not ctx.meeting_id:
            return SkillResult.fail("회의 범위가 없습니다.", "validation")

        query = (args.get("query") or "").strip()
        scope = args.get("scope") or "all"
        evidence: list[dict] = []

        if scope in ("utterance", "all"):
            evidence += self._utterances(query, ctx)
        if scope in ("agenda", "all"):
            evidence += self._agendas(query, ctx)
        if scope in ("summary", "all"):
            evidence += self._summary(ctx)

        return SkillResult(
            ok=True,
            message=f"{len(evidence)}건을 찾았습니다." if evidence else "회의 기록이 없습니다.",
            data={"items": [{k: e[k] for k in
                             ("source_type", "title_snapshot", "excerpt")}
                            for e in evidence]},
            evidence=evidence,
        )

    def _utterances(self, query: str, ctx: SkillContext) -> list[dict]:
        from apps.meetings.models import Utterance

        qs = Utterance.objects.filter(meeting_id=ctx.meeting_id)
        if query:
            qs = qs.filter(body__icontains=query)

        rows = []
        # 최근 발언부터 봅니다. 회의 맥락은 뒤로 갈수록 확정에 가깝습니다.
        for u in qs.order_by("-id")[:LIMIT]:
            rows.append({
                "source_type": "meeting",
                "source_id": str(u.id),
                "title_snapshot": f"{u.participant_name} 의 발언",
                "excerpt": _excerpt(u.body),
                # 회의 발언은 본인 것이라도 '내 기록' 이 아닙니다. 그 자리에서 한 말이지
                # 대리인이 근거로 삼을 작업 기록이 아니기 때문입니다.
                "owner_is_principal": ctx.is_principal(u.participant_id),
                "updated_at": (u.spoken_at or timezone.now()).isoformat(),
                "staleness_days": _staleness(u.spoken_at),
                "match": "direct" if query else "partial",
                "status": "",
                "visibility": "team",
                "confidence": None,
                "requires_discussion": False,
            })
        return rows

    def _agendas(self, query: str, ctx: SkillContext) -> list[dict]:
        from apps.meetings.models import Agenda

        qs = Agenda.objects.filter(meeting_id=ctx.meeting_id)
        if query:
            qs = qs.filter(Q(title__icontains=query) | Q(content__icontains=query))

        rows = []
        for a in qs.order_by("sort_order")[:LIMIT]:
            rows.append({
                "source_type": "meeting",
                "source_id": str(a.id),
                "title_snapshot": a.title,
                "excerpt": _excerpt(a.content),
                "owner_is_principal": ctx.is_principal(a.owner_id) if a.owner_id else False,
                "updated_at": a.updated_at.isoformat(),
                "staleness_days": _staleness(a.updated_at),
                "match": "direct" if query and query.lower() in a.title.lower() else "partial",
                "status": a.status,
                "visibility": "team",
                "confidence": None,
                "requires_discussion": False,
            })
        return rows

    def _summary(self, ctx: SkillContext) -> list[dict]:
        from apps.meetings.models import MeetingSummary

        s = MeetingSummary.objects.filter(meeting_id=ctx.meeting_id).first()
        if not s:
            return []

        # 세 갈래를 한 항목으로 합칩니다. 나눠 넣으면 같은 회의 요약이 근거 3건으로
        # 세어져, 유보 판정이 '근거가 많다' 고 착각합니다.
        parts = []
        for label, rows in (("발견한 문제", s.discovered_issues),
                            ("변동사항", s.changes),
                            ("이후 계획", s.next_plans)):
            if rows:
                parts.append(f"{label}: " + " / ".join(str(r) for r in rows[:3]))
        if not parts and not s.one_line:
            return []

        return [{
            "source_type": "meeting",
            "source_id": str(s.meeting_id),
            "title_snapshot": "회의 요약",
            "excerpt": _excerpt(s.one_line or " · ".join(parts), 400),
            "owner_is_principal": False,
            "updated_at": s.updated_at.isoformat(),
            "staleness_days": _staleness(s.updated_at),
            "match": "partial",
            "status": "",
            "visibility": "team",
            "confidence": None,
            "requires_discussion": False,
        }]

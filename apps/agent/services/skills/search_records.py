"""
기록 검색.

대리인이 답하려면 근거가 필요합니다. 여기서 모은 것이 `AgentRun.evidence` 가 되고,
그것이 그대로 유보 판정의 입력입니다.

## match 라벨을 코드가 붙이는 이유

설계 초안에서는 LLM 이 관련도를 라벨링하기로 했습니다. 구현하면서 바꿨습니다.

- LLM 왕복이 한 번 더 늘어 비용과 지연이 커집니다
- **모델은 자기가 찾은 것을 관련 있다고 말하는 경향**이 있습니다. 관련도까지
  맡기면 유보 판정의 입력이 낙관적으로 기울어, 판정을 코드로 뺀 의미가 줄어듭니다

그래서 **검색어가 어디에 걸렸는지**로 결정합니다.

    제목에 걸림        direct     "그 작업" 을 직접 가리킴
    본문에만 걸림      partial    언급은 되지만 주제가 아님
    카테고리로만 걸림  inferred   추측

기계적이고 설명 가능하며 테스트할 수 있습니다. LLM 재라벨링은 나중에 얹을 수 있고,
그때도 이 값이 기본값으로 남습니다.
"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from .base import SkillBase, SkillContext, SkillKind, SkillResult

#: 한 번에 가져올 상한. 근거가 많다고 답이 좋아지지 않고 토큰만 늘어납니다.
LIMIT = 8

MATCH_DIRECT = "direct"
MATCH_PARTIAL = "partial"
MATCH_INFERRED = "inferred"


def _staleness(dt) -> int:
    if not dt:
        return 0
    return max(0, (timezone.now() - dt).days)


#: 토큰으로 쪼갤 때 버릴 말. 이것만 걸리면 아무 기록이나 다 걸립니다.
_STOP = {"작업", "진행", "상황", "현황", "어디", "지금", "관련", "내용", "확인",
         "무엇", "어떻게", "언제", "정도", "관해", "대해"}
_MIN_TOKEN = 2


def _tokens(query: str) -> list[str]:
    """
    검색어를 토큰으로 쪼갭니다.

    모델은 `"DB 스키마 작업 진행상황"` 처럼 문장에 가까운 검색어를 만듭니다.
    저장된 제목은 `"team_members 마이그레이션"` 이라 통째로는 절대 안 걸립니다.
    한국어라 형태소 분석 없이는 완벽할 수 없지만, 공백으로 쪼개고 흔한 말을
    걸러내는 것만으로 대부분 잡힙니다.
    """
    out = []
    for raw in (query or "").replace(",", " ").split():
        t = raw.strip().lower()
        if len(t) >= _MIN_TOKEN and t not in _STOP:
            out.append(t)
    return out


def _match_of(query: str, title: str, body: str) -> str:
    """
    관련도.

    전체 검색어가 통째로 걸리면 가장 강하고, 토큰 하나만 걸리면 약합니다.
    """
    q = (query or "").strip().lower()
    t, b = (title or "").lower(), (body or "").lower()
    if not q:
        return MATCH_INFERRED

    if q in t:
        return MATCH_DIRECT
    if q in b:
        return MATCH_PARTIAL

    toks = _tokens(query)
    if any(tok in t for tok in toks):
        return MATCH_DIRECT
    if any(tok in b for tok in toks):
        return MATCH_PARTIAL
    return MATCH_INFERRED


def _excerpt(text: str, limit: int = 200) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit]


class SearchRecordsSkill(SkillBase):
    name = "search_records"
    kind = SkillKind.READ
    description = (
        "작업 현황·계획·생각과 프로젝트 문서를 검색합니다. "
        "'지금 어디까지 됐나', '무엇이 막혀 있나', '어떻게 하려고 했나' 에 답할 때 씁니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색어. 핵심 명사 위주로"},
            "kinds": {
                "type": "array",
                "items": {"type": "string",
                          "enum": ["work", "plan", "thought", "document"]},
                "description": "생략하면 전부 찾습니다.",
            },
        },
        "required": ["query"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        query = (args.get("query") or "").strip()
        if not query:
            return SkillResult.fail("query 는 필수입니다.", "validation")
        if not ctx.project_id:
            return SkillResult.fail("프로젝트 범위가 없습니다.", "validation")

        kinds = args.get("kinds") or ["work", "plan", "thought", "document"]
        evidence: list[dict] = []

        if "work" in kinds:
            evidence += self._states("work", query, ctx)
        if "plan" in kinds:
            evidence += self._states("plan", query, ctx)
        if "thought" in kinds:
            evidence += self._states("thought", query, ctx)
        if "document" in kinds:
            evidence += self._documents(query, ctx)

        # 직접 걸린 것부터, 그다음 최신순. LLM 이 앞쪽을 더 본다는 점을 이용합니다.
        order = {MATCH_DIRECT: 0, MATCH_PARTIAL: 1, MATCH_INFERRED: 2}
        evidence.sort(key=lambda e: (order.get(e["match"], 9), e["staleness_days"]))
        evidence = evidence[:LIMIT]

        # 0건도 성공입니다. "찾았는데 없었다" 와 "검색이 터졌다" 는 다릅니다 —
        # 판정 단계에서 구별해야 하므로 여기서 실패로 만들지 않습니다.
        return SkillResult(
            ok=True,
            message=f"{len(evidence)}건을 찾았습니다." if evidence else "관련 기록이 없습니다.",
            data={"items": [{k: e[k] for k in
                             ("source_type", "title_snapshot", "excerpt", "status")}
                            for e in evidence]},
            evidence=evidence,
        )

    # ── work / plan / thought ──────────────────────────────
    def _states(self, kind: str, query: str, ctx: SkillContext) -> list[dict]:
        from apps.states.models import PlanItem, ThoughtItem, WorkItem

        model, fields = {
            "work": (WorkItem, ("title", "summary")),
            "plan": (PlanItem, ("title", "")),
            "thought": (ThoughtItem, ("topic", "content")),
        }[kind]

        title_f, body_f = fields
        qs = self._visible(model.objects.filter(project_id=ctx.project_id), ctx)

        cond = self._condition(query, title_f, body_f)
        hit = qs.filter(cond) if cond else qs.none()

        if not hit.exists():
            # 아무것도 안 걸리면 **본인의 최근 기록**을 대신 보여줍니다.
            #
            # 빈손으로 돌려주면 모델은 검색어만 바꿔 가며 헛돕니다. 무엇이 있는지
            # 보여주면 다음 호출에서 정확한 말로 다시 찾습니다.
            #
            # 이 결과는 전부 `inferred` 로 라벨링되므로, 그대로 답으로 가면
            # 유보 규칙 R3 에 걸립니다. 근거가 아니라 **길잡이**입니다.
            hit = qs.filter(owner_id=ctx.principal_id)
        qs = hit

        rows = []
        for obj in qs.select_related("owner").order_by("-updated_at")[:LIMIT]:
            title = getattr(obj, title_f)
            body = getattr(obj, body_f, "") if body_f else ""
            rows.append({
                "source_type": kind,
                "source_id": str(obj.id),
                "title_snapshot": title,
                "excerpt": _excerpt(body),
                "owner_is_principal": ctx.is_principal(obj.owner_id),
                "updated_at": obj.updated_at.isoformat(),
                "staleness_days": _staleness(obj.updated_at),
                "match": _match_of(query, title, body),
                "status": getattr(obj, "status", ""),
                "visibility": obj.visibility,
                "confidence": getattr(obj, "confidence", None),
                "requires_discussion": getattr(obj, "requires_discussion", False),
            })
        return rows

    # ── 문서 ───────────────────────────────────────────────
    def _documents(self, query: str, ctx: SkillContext) -> list[dict]:
        from apps.documents.models import Document

        qs = self._visible(
            Document.objects.filter(project_id=ctx.project_id), ctx)
        cond = self._condition(query, "title", "content")
        qs = qs.filter(cond) if cond else qs.none()

        rows = []
        for doc in qs.order_by("-updated_at")[:LIMIT]:
            rows.append({
                "source_type": "document",
                "source_id": str(doc.id),
                "title_snapshot": doc.title,
                # summary 를 먼저 쓰는 이유 — 본문 앞부분은 대개 머리말이라
                # 무엇에 관한 문서인지 드러나지 않습니다.
                "excerpt": _excerpt(doc.summary or doc.content),
                "owner_is_principal": ctx.is_principal(doc.owner_id),
                "updated_at": doc.updated_at.isoformat(),
                "staleness_days": _staleness(doc.updated_at),
                "match": _match_of(query, doc.title, doc.content),
                "status": "",
                "visibility": doc.visibility,
                "confidence": None,
                "requires_discussion": False,
            })
        return rows

    # ── 검색 조건 ──────────────────────────────────────────
    @staticmethod
    def _condition(query: str, title_f: str, body_f: str) -> Q | None:
        """
        전체 문구 먼저, 그다음 토큰.

        모델이 만든 검색어는 문장에 가까워 통째로는 잘 안 걸립니다.
        토큰 하나만 걸려도 후보로 올리고, 관련도는 `match` 가 구분합니다.
        """
        fields = [title_f, "category"] + ([body_f] if body_f else [])

        cond = Q()
        for f in fields:
            cond |= Q(**{f"{f}__icontains": query})
        for tok in _tokens(query):
            for f in fields:
                cond |= Q(**{f"{f}__icontains": tok})
        return cond or None

    # ── 비공개 처리 ────────────────────────────────────────
    @staticmethod
    def _visible(qs, ctx: SkillContext):
        """
        남의 비공개 기록은 **존재 여부조차 반환하지 않습니다.**

        본인 것은 `allow_private` 에 달렸습니다. 회의 대리에서는 꺼내지 않고,
        본인이 자기 AI 와 대화할 때는 꺼냅니다.
        """
        if ctx.allow_private:
            return qs.filter(Q(visibility="team") | Q(owner_id=ctx.principal_id))
        return qs.filter(visibility="team")

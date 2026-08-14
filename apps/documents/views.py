"""
문서.

세 가지가 계약보다 조입니다. 전부 사고를 막기 위한 것입니다.

* **비밀키 마스킹은 저장 전에 합니다.** 색인에 들어가고 나서 지우면
  이미 검색 결과와 대리인 발언에 실려 나간 뒤입니다.
* **수정은 새 버전을 만듭니다.** 덮어쓰지 않아 되짚을 수 있습니다.
* **PRIVATE 은 404 입니다.** 403 을 주면 "그런 문서가 있다"가 새어 나갑니다.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.permissions import project_membership
from apps.common.views import listing
from apps.orgs.models import TeamRole
from config.errors import BordoError

from .models import Document, DocumentVersion, Visibility, content_hash, mask_secrets
from .serializers import (DocumentSerializer, DocumentSummarySerializer,
                          DocumentVersionSerializer)

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)


def _visible(qs, user):
    return qs.filter(Q(visibility=Visibility.TEAM) | Q(owner=user))


def _load(user, document_id):
    doc = (Document.objects.filter(pk=document_id)
           .select_related("project", "owner").first())
    if not doc:
        raise BordoError("DOCUMENT_NOT_FOUND", details={"document_id": str(document_id)})
    _, member = project_membership(user, doc.project_id)
    if not doc.visible_to(user):
        # 존재 자체를 숨깁니다.
        raise BordoError("DOCUMENT_NOT_FOUND", details={"document_id": str(document_id)})
    return doc, member


def _snapshot(doc, source, author, *, restored_from=None, reason=""):
    DocumentVersion.objects.create(
        document=doc, version=doc.version, title=doc.title, content=doc.content,
        sections=doc.sections, hash=doc.hash, source=source, author=author,
        restored_from=restored_from, reason=reason)


def _summarize(content, sections):
    """
    검색 미리보기용 한 줄.

    AI 요약은 2차입니다. 그때까지는 첫 문단을 씁니다 — 빈 값보다는
    무슨 문서인지 알아볼 수 있는 게 낫습니다.
    """
    if sections:
        first = next((s.get("one_line_summary") for s in sections
                      if isinstance(s, dict) and s.get("one_line_summary")), None)
        if first:
            return first[:300]
    text = " ".join((content or "").split())
    return text[:300]


# ─────────────────────────────────────────── 목록 · 생성
@api_view(["GET", "POST"])
def documents(request, project_id):
    project, _ = project_membership(request.user, project_id)

    if request.method == "GET":
        qs = _visible(Document.objects.filter(project=project), request.user)
        if request.query_params.get("category"):
            qs = qs.filter(category=request.query_params["category"])
        vis = request.query_params.get("visibility")
        if vis:
            # `private` 을 물어도 남의 것은 안 나옵니다.
            qs = qs.filter(visibility=vis)
        owner = request.query_params.get("owner")
        if owner:
            qs = qs.filter(owner=request.user) if owner == "me" else qs.filter(owner_id=owner)
        q = (request.query_params.get("q") or "").strip()
        if q:
            # 임베딩 검색은 2차. 지금은 제목·본문 부분 일치입니다.
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))

        page = max(1, int(request.query_params.get("page", 1)))
        size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        total = qs.count()
        rows = list(qs.select_related("owner")[(page - 1) * size: page * size])
        return Response(listing(
            DocumentSummarySerializer(rows, many=True).data, count=total,
            extra={"page": page, "page_size": size}))

    title = (request.data.get("title") or "").strip()
    if not title:
        raise BordoError("VALIDATION_ERROR", "title 은 필수입니다.")
    raw = request.data.get("content") or ""
    sections = request.data.get("sections") or []
    if not raw and not sections:
        raise BordoError("VALIDATION_ERROR", "content 또는 sections 중 하나는 있어야 합니다.")

    content, masked = mask_secrets(raw)
    sections, masked_in_sections = _mask_sections(sections)
    masked += masked_in_sections
    digest = content_hash(content)

    # 같은 내용을 다시 올리면 새 문서를 만들지 않습니다.
    existing = Document.objects.filter(project=project, hash=digest,
                                       title=title).first()
    if existing:
        return Response(DocumentSerializer(existing).data, status=200)

    with transaction.atomic():
        doc = Document.objects.create(
            project=project, title=title,
            category=request.data.get("category") or "",
            visibility=request.data.get("visibility") or Visibility.TEAM,
            owner=request.user, content=content, sections=sections,
            summary=_summarize(content, sections), hash=digest,
            masked_secrets=masked, version=1)
        _snapshot(doc, DocumentVersion.Source.WEB_EDIT, request.user)

    publish(project.id, "document.created", {"document_id": str(doc.id)})
    body = DocumentSerializer(doc).data
    # 색인은 2차(B)에서 붙습니다. 화면이 `색인 중` 을 그릴 수 있게 자리를 둡니다.
    body["indexing"] = {"status": "QUEUED", "task_id": None}
    return Response(body, status=201)


def _mask_sections(sections):
    if not isinstance(sections, list):
        raise BordoError("VALIDATION_ERROR", "sections 는 배열입니다.")
    out, total = [], 0
    for s in sections:
        if not isinstance(s, dict):
            raise BordoError("VALIDATION_ERROR",
                             "sections 항목은 {heading, one_line_summary, body} 입니다.")
        body, n = mask_secrets(s.get("body") or "")
        total += n
        out.append({"heading": s.get("heading") or "",
                    "one_line_summary": s.get("one_line_summary") or "",
                    "body": body})
    return out, total


# ─────────────────────────────────────────── 상세 · 수정 · 삭제
@api_view(["GET", "PATCH", "DELETE"])
def document_detail(request, document_id):
    doc, member = _load(request.user, document_id)

    if request.method == "GET":
        return Response(DocumentSerializer(doc).data)

    is_owner = doc.owner_id == request.user.id
    if request.method == "PATCH":
        if not is_owner and member.team_role not in ADMINS:
            raise BordoError("DOCUMENT_ACCESS_DENIED", "소유자 또는 OWNER · ADMIN 만 가능합니다.")

        want = request.headers.get("If-Match")
        if want:
            try:
                want = int(want.strip('"'))
            except ValueError:
                raise BordoError("VALIDATION_ERROR", "If-Match 는 정수 version 입니다.")
            if want != doc.version:
                raise BordoError(
                    "REFERENCED_BY_OTHERS",
                    "그사이 다른 사람이 고쳤습니다. 다시 불러온 뒤 시도하십시오.",
                    details={"your_version": want, "current_version": doc.version},
                    status=409)

        masked = 0
        if "content" in request.data:
            doc.content, masked = mask_secrets(request.data["content"] or "")
        if "sections" in request.data:
            doc.sections, n = _mask_sections(request.data["sections"])
            masked += n
        for f in ("title", "category", "visibility"):
            if f in request.data:
                setattr(doc, f, request.data[f])

        with transaction.atomic():
            doc.hash = content_hash(doc.content)
            doc.summary = _summarize(doc.content, doc.sections)
            doc.masked_secrets = masked
            doc.version += 1
            doc.indexed = False          # 내용이 바뀌었으니 색인을 다시 타야 합니다
            doc.save()
            _snapshot(doc, DocumentVersion.Source.WEB_EDIT, request.user)

        publish(doc.project_id, "document.updated",
                {"document_id": str(doc.id), "version": doc.version})
        return Response(DocumentSerializer(doc).data)

    # ── DELETE — 소프트 삭제
    if not is_owner and member.team_role not in ADMINS:
        raise BordoError("DOCUMENT_ACCESS_DENIED", "소유자 또는 OWNER · ADMIN 만 가능합니다.")
    deleted_at = doc.soft_delete()
    # 색인에서는 즉시 뺍니다 — 지운 문서가 검색·인용에 잡히면 안 됩니다.
    Document.objects.filter(pk=doc.pk).update(indexed=False)
    publish(doc.project_id, "document.deleted", {"document_id": str(doc.id)})
    return Response({"id": str(doc.id), "deleted_at": deleted_at,
                     "restorable_until": timezone.now() + timezone.timedelta(days=30)})


# ─────────────────────────────────────────── 버전
@api_view(["GET"])
def versions(request, document_id):
    doc, _ = _load(request.user, document_id)
    rows = list(doc.versions.select_related("author")[:100])
    return Response(listing(DocumentVersionSerializer(rows, many=True).data,
                            extra={"document_id": str(doc.id),
                                   "current_version": doc.version}))


@api_view(["GET"])
def version_detail(request, document_id, version):
    """특정 버전의 본문. 되돌리기 전에 내용을 확인하는 자리입니다."""
    doc, _ = _load(request.user, document_id)
    row = doc.versions.filter(version=version).first()
    if not row:
        raise BordoError("STATE_NOT_FOUND", "그 버전은 없습니다.",
                         details={"version": version,
                                  "current_version": doc.version})
    body = DocumentVersionSerializer(row).data
    body.update({"content": row.content, "sections": row.sections})
    return Response(body)


@api_view(["POST"])
def restore(request, document_id):
    """
    이전 버전으로 되돌립니다.

    **덮어쓰지 않고 새 버전을 만듭니다.** 되돌린 사실 자체가 이력에 남아야
    "왜 갑자기 예전 내용이지"를 나중에 설명할 수 있습니다.
    """
    doc, member = _load(request.user, document_id)
    if doc.owner_id != request.user.id and member.team_role not in ADMINS:
        raise BordoError("DOCUMENT_ACCESS_DENIED", "소유자 또는 OWNER · ADMIN 만 가능합니다.")

    target = request.data.get("version")
    if not target:
        raise BordoError("VALIDATION_ERROR", "version 은 필수입니다.")
    snapshot = doc.versions.filter(version=target).first()
    if not snapshot:
        raise BordoError("STATE_NOT_FOUND", "그 버전은 없습니다.",
                         details={"version": target, "current_version": doc.version})
    if int(target) == doc.version:
        raise BordoError("DUPLICATE_EVENT", "이미 그 버전입니다.")

    reason = request.data.get("reason") or ""
    with transaction.atomic():
        doc.title, doc.content = snapshot.title, snapshot.content
        doc.sections, doc.hash = snapshot.sections, snapshot.hash
        doc.summary = _summarize(doc.content, doc.sections)
        doc.version += 1
        doc.indexed = False
        doc.save()
        _snapshot(doc, DocumentVersion.Source.RESTORE, request.user,
                  restored_from=int(target), reason=reason)

    publish(doc.project_id, "document.restored",
            {"document_id": str(doc.id), "version": doc.version})
    return Response({"id": str(doc.id), "restored_from": int(target),
                     "version": doc.version, "content_hash": doc.hash,
                     "reason": reason, "created_at": timezone.now()})


@api_view(["POST"])
def restore_deleted(request, document_id):
    """
    소프트 삭제된 문서 복구.

    유예 기간이 지났으면 `404` 입니다.
    """
    doc = (Document.all_objects.filter(pk=document_id)
           .select_related("project", "owner").first())
    if not doc or not doc.deleted_at:
        raise BordoError("DOCUMENT_NOT_FOUND", details={"document_id": str(document_id)})
    _, member = project_membership(request.user, doc.project_id)
    if doc.owner_id != request.user.id and member.team_role not in ADMINS:
        raise BordoError("DOCUMENT_ACCESS_DENIED", "소유자 또는 OWNER · ADMIN 만 가능합니다.")

    grace = doc.deleted_at + timezone.timedelta(days=30)
    if timezone.now() > grace:
        raise BordoError("DOCUMENT_NOT_FOUND",
                         "복구 유예 기간이 지났습니다.",
                         details={"restorable_until": grace})
    doc.restore()
    publish(doc.project_id, "document.restored", {"document_id": str(doc.id)})
    return Response(DocumentSerializer(doc).data)

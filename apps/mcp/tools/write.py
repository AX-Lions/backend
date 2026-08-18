"""
1단계 쓰기 도구 3종.

읽기 도구는 일부러 없습니다. 이 토큰은 개발자 컴퓨터 설정 파일에 평문으로 앉는
장기 자격증명이라, 새더라도 **가져갈 수 있는 게 없어야** 피해가 "조작"에 그칩니다.
읽기를 붙이는 2단계에는 토큰 만료가 같이 와야 합니다.

세 도구 모두 **재시도가 안전**하게 만듭니다. 개인 AI 는 같은 말을 여러 번 하므로
재시도를 사고로 만들지 않는 게 아니라 재시도가 원래 동작이 되도록 설계합니다.
"""
from __future__ import annotations

from django.db import transaction

from apps.common.events import publish
from apps.documents.models import Document, DocumentVersion, Visibility as DocVisibility
from apps.documents.models import content_hash, mask_secrets
from apps.documents.views import _snapshot as snapshot_document
from apps.documents.views import _summarize as summarize_document
from apps.states.models import Source, Visibility, WorkItem, WorkStatus
from apps.states.services import RULES, apply_fields, log_activity, validate
from config.errors import BordoError

from .base import McpTool, ToolContext, ToolResult, arg_str
from .projects import project_brief, resolve_project
from .registry import registry

STATUS_LABEL = {"TODO": "예정", "IN_PROGRESS": "진행 중", "BLOCKED": "막힘", "DONE": "완료"}
WORK_FIELDS = ("summary", "progress", "status", "blockers", "category", "visibility")


# ─────────────────────────────────────────── bordo_record_work
class RecordWork(McpTool):
    def __init__(self):
        super().__init__(
            name="bordo_record_work",
            description=("지금 하고 있는 일을 Bordo 에 기록합니다. 같은 프로젝트에 같은 "
                         "제목이 이미 있으면 새로 만들지 않고 갱신합니다."),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 200,
                              "description": "무엇을 하고 있는지 한 줄"},
                    "summary": {"type": "string"},
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                    "status": {"type": "string", "enum": list(WorkStatus.values),
                               "default": "IN_PROGRESS"},
                    "blockers": {"type": "array", "items": {"type": "string"},
                                 "description": "막힌 이유. '왜 안 되고 있나'의 답"},
                    "category": {"type": "string"},
                    "visibility": {"type": "string", "enum": list(Visibility.values),
                                   "default": "team"},
                    "project_id": {"type": "string",
                                   "description": "생략하면 최근에 연 프로젝트"},
                },
                "required": ["title"],
            })

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        title = arg_str(args, "title", required=True, max_len=200)
        project, resolved_by = resolve_project(ctx.user, args.get("project_id"))

        data = {k: args[k] for k in WORK_FIELDS if k in args and args[k] is not None}
        rules = RULES["work"]
        validate(rules, data)

        with transaction.atomic():
            obj = (WorkItem.objects.select_for_update()
                   .filter(project=project, owner=ctx.user, title=title).first())
            if obj is None:
                data.setdefault("status", WorkStatus.IN_PROGRESS)
                obj = WorkItem.objects.create(project=project, owner=ctx.user, title=title,
                                              source=Source.MCP, **data)
                action, changed = "CREATED", {"title": title}
                log_activity(project, ctx.user, "work.created", obj,
                             {"title": title, "source": Source.MCP})
            else:
                changed = apply_fields(obj, data, WORK_FIELDS)
                if changed:
                    obj.source = Source.MCP
                    obj.save()
                    log_activity(project, ctx.user, "work.updated", obj,
                                 {**changed, "source": Source.MCP})
                action = "UPDATED" if changed else "UNCHANGED"

        if action != "UNCHANGED":
            publish(project.id, f"work.{action.lower()}",
                    {"id": str(obj.id), "owner_id": str(ctx.user.id), "source": Source.MCP})

        verb = {"CREATED": "기록했습니다", "UPDATED": "갱신했습니다",
                "UNCHANGED": "이미 같은 내용으로 기록돼 있습니다"}[action]
        text = (f"{project.name} 프로젝트에 「{obj.title}」을(를) "
                f"{STATUS_LABEL[obj.status]} {obj.progress}% 로 {verb}.")
        return ToolResult(text=text, structured={
            "work_item_id": str(obj.id), "action": action, "title": obj.title,
            "status": obj.status, "progress": obj.progress,
            "project": project_brief(project, resolved_by), "source": Source.MCP,
        })


# ─────────────────────────────────────────── bordo_upload_document
class UploadDocument(McpTool):
    def __init__(self):
        super().__init__(
            name="bordo_upload_document",
            description=("문서를 Bordo 에 올립니다. 비밀키는 저장 전에 마스킹되고 몇 건을 "
                         "지웠는지 돌려줍니다. 같은 제목·같은 내용이 이미 있으면 새로 "
                         "만들지 않습니다."),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 200},
                    "content": {"type": "string", "description": "마크다운 원문"},
                    "category": {"type": "string"},
                    "visibility": {"type": "string", "enum": list(DocVisibility.values),
                                   "default": "team"},
                    "project_id": {"type": "string",
                                   "description": "생략하면 최근에 연 프로젝트"},
                },
                "required": ["title", "content"],
            })

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        title = arg_str(args, "title", required=True, max_len=200)
        raw = args.get("content")
        if not isinstance(raw, str) or not raw.strip():
            raise BordoError("VALIDATION_ERROR", "content 는 비어 있지 않은 문자열이어야 합니다.")
        visibility = arg_str(args, "visibility", default=DocVisibility.TEAM) or DocVisibility.TEAM
        if visibility not in DocVisibility.values:
            raise BordoError("VALIDATION_ERROR", "visibility 는 team 또는 private 입니다.",
                             details={"visibility": visibility})
        category = arg_str(args, "category", max_len=40)
        project, resolved_by = resolve_project(ctx.user, args.get("project_id"))

        content, masked = mask_secrets(raw)
        digest = content_hash(content)

        # 같은 내용을 다시 올리면 새 문서를 만들지 않습니다 — 웹 업로드와 같은 규칙입니다.
        existing = Document.objects.filter(project=project, hash=digest, title=title).first()
        if existing:
            doc, action = existing, "UNCHANGED"
        else:
            with transaction.atomic():
                doc = Document.objects.create(
                    project=project, title=title, category=category,
                    visibility=visibility, owner=ctx.user, content=content,
                    summary=summarize_document(content, []), hash=digest,
                    masked_secrets=masked, version=1)
                snapshot_document(doc, DocumentVersion.Source.MCP_PUSH, ctx.user)
            publish(project.id, "document.created",
                    {"document_id": str(doc.id), "source": "mcp"})
            action = "CREATED"

        text = (f"{project.name} 프로젝트에 「{doc.title}」을(를) 올렸습니다."
                if action == "CREATED" else
                f"「{doc.title}」은(는) 이미 같은 내용으로 올라가 있습니다.")
        if masked:
            text += f" 비밀키로 보이는 값 {masked}건을 지웠습니다 — 사용자에게 알리십시오."
        return ToolResult(text=text, structured={
            "document_id": str(doc.id), "action": action, "title": doc.title,
            "version": doc.version, "content_hash": digest, "masked_secrets": masked,
            "visibility": doc.visibility, "project": project_brief(project, resolved_by),
        })


# ─────────────────────────────────────────── bordo_complete_work
class CompleteWork(McpTool):
    def __init__(self):
        super().__init__(
            name="bordo_complete_work",
            description=("하고 있던 일을 완료로 표시합니다. work_item_id 나 title 중 "
                         "하나로 대상을 지정합니다."),
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {"type": "string"},
                    "title": {"type": "string",
                              "description": "work_item_id 가 없을 때 제목으로 찾습니다"},
                    "note": {"type": "string", "description": "완료하며 남길 한 줄"},
                    "project_id": {"type": "string",
                                   "description": "title 로 찾을 때 범위. 생략하면 최근 프로젝트"},
                },
            })

    def _find(self, args: dict, ctx: ToolContext) -> tuple[WorkItem, str]:
        work_id = arg_str(args, "work_item_id")
        title = arg_str(args, "title", max_len=200)
        if not work_id and not title:
            raise BordoError("VALIDATION_ERROR",
                             "무엇을 완료했는지 지정하십시오 — work_item_id 또는 title.")
        if work_id:
            obj = WorkItem.objects.filter(pk=work_id, owner=ctx.user).select_related("project").first()
            if obj is None:
                raise BordoError("STATE_NOT_FOUND", "그 id 의 내 작업이 없습니다.",
                                 details={"work_item_id": work_id})
            resolve_project(ctx.user, obj.project_id)      # 아직 참여자인지
            return obj, "argument"
        project, resolved_by = resolve_project(ctx.user, args.get("project_id"))
        rows = list(WorkItem.objects.filter(project=project, owner=ctx.user, title=title)
                    .select_related("project").order_by("-updated_at")[:2])
        if not rows:
            raise BordoError("STATE_NOT_FOUND", f"「{title}」이라는 내 작업이 없습니다.",
                             details={"title": title, "project_id": str(project.id)})
        return rows[0], resolved_by

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        note = arg_str(args, "note", max_len=500)
        obj, resolved_by = self._find(args, ctx)
        project = obj.project

        if obj.status == WorkStatus.DONE and obj.progress == 100:
            action = "ALREADY_DONE"
        else:
            with transaction.atomic():
                changed = apply_fields(obj, {"status": WorkStatus.DONE, "progress": 100},
                                       ("status", "progress"))
                obj.source = Source.MCP
                obj.save()
                detail = {**changed, "source": Source.MCP}
                if note:
                    detail["note"] = note
                log_activity(project, ctx.user, "work.completed", obj, detail)
            publish(project.id, "work.updated",
                    {"id": str(obj.id), "changed": ["status", "progress"], "source": Source.MCP})
            action = "COMPLETED"

        text = (f"「{obj.title}」을(를) 완료로 표시했습니다." if action == "COMPLETED"
                else f"「{obj.title}」은(는) 이미 완료 상태입니다.")
        return ToolResult(text=text, structured={
            "work_item_id": str(obj.id), "action": action, "title": obj.title,
            "status": obj.status, "progress": obj.progress,
            "project": project_brief(project, resolved_by), "source": Source.MCP,
        })


registry.register(RecordWork())
registry.register(UploadDocument())
registry.register(CompleteWork())

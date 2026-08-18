"""
쓰기 도구 3종.

보는 것: 재시도가 안전한가(같은 제목 → 갱신, 같은 내용 → 안 만듦, 이미 완료 → 성공),
비밀키가 지워지고 건수가 응답에 실리는가, 남의 프로젝트에 못 쓰는가,
기본 프로젝트로 갔을 때 그 사실이 응답에 남는가.
"""
from apps.documents.models import Document, DocumentVersion
from apps.orgs.models import Project, RecentProject
from apps.states.models import ActivityEvent, Source, WorkItem

from .tests_base import McpTestBase


class RecordWorkTest(McpTestBase):

    def test_creates_with_mcp_source_and_default_project(self):
        res, body = self.call("bordo_record_work",
                              {"title": "MCP 연동", "progress": 80, "status": "IN_PROGRESS"})
        self.assertEqual(res.status_code, 200)
        r = self.result(body)
        self.assertFalse(r["isError"])
        self.assertIn("80%", r["content"][0]["text"])
        s = self.structured(body)
        self.assertEqual(s["action"], "CREATED")
        self.assertEqual(s["source"], "mcp")
        self.assertEqual(s["project"]["id"], str(self.project.id))
        self.assertEqual(s["project"]["resolved_by"], "default")

        item = WorkItem.objects.get(pk=s["work_item_id"])
        self.assertEqual(item.source, Source.MCP)
        self.assertEqual(item.owner, self.user)
        self.assertEqual(item.progress, 80)
        self.assertTrue(ActivityEvent.objects.filter(target_id=item.id, kind="work.created").exists())

    def test_same_title_updates_instead_of_duplicating(self):
        self.call("bordo_record_work", {"title": "MCP 연동", "progress": 30})
        _, body = self.call("bordo_record_work", {"title": "MCP 연동", "progress": 60,
                                                  "blockers": ["헤더 검증"]})
        s = self.structured(body)
        self.assertEqual(s["action"], "UPDATED")
        self.assertEqual(WorkItem.objects.filter(owner=self.user, title="MCP 연동").count(), 1)
        self.assertEqual(WorkItem.objects.get(pk=s["work_item_id"]).blockers, ["헤더 검증"])

        _, body = self.call("bordo_record_work", {"title": "MCP 연동", "progress": 60})
        self.assertEqual(self.structured(body)["action"], "UNCHANGED")

    def test_explicit_project_is_marked_argument(self):
        _, body = self.call("bordo_record_work",
                            {"title": "봇 작업", "project_id": str(self.project2.id)})
        s = self.structured(body)
        self.assertEqual(s["project"]["id"], str(self.project2.id))
        self.assertEqual(s["project"]["resolved_by"], "argument")

    def test_foreign_project_is_denied(self):
        # 이 사용자는 팀 OWNER 라 같은 팀 프로젝트는 관리자로 통과합니다 — 다른 팀으로 시험합니다
        from apps.orgs.models import Team
        t2 = Team.objects.create(name="다른 팀", created_by=self.other)
        stranger = Project.objects.create(team=t2, name="남의 것", created_by=self.other)
        _, body = self.call("bordo_record_work", {"title": "x", "project_id": str(stranger.id)})
        r = self.result(body)
        self.assertTrue(r["isError"])
        self.assertEqual(r["structuredContent"]["code"], "TEAM_ACCESS_DENIED")
        self.assertFalse(WorkItem.objects.filter(project=stranger).exists())

    def test_validation_errors(self):
        for args in ({"title": ""}, {"title": "x", "status": "NOPE"},
                     {"title": "x", "visibility": "secret"}, {"title": "x", "blockers": "no"},
                     {"title": "x" * 201}):
            _, body = self.call("bordo_record_work", args)
            self.assertTrue(self.result(body)["isError"], args)

    def test_no_default_project_asks_for_id(self):
        RecentProject.objects.filter(user=self.user).delete()      # 최근 없음, 프로젝트는 2개
        _, body = self.call("bordo_record_work", {"title": "x"})
        r = self.result(body)
        self.assertTrue(r["isError"])
        self.assertIn("project_id", r["content"][0]["text"])
        ids = {p["id"] for p in r["structuredContent"]["details"]["projects"]}
        self.assertEqual(ids, {str(self.project.id), str(self.project2.id)})


class UploadDocumentTest(McpTestBase):

    def test_creates_document_with_mcp_push_version(self):
        _, body = self.call("bordo_upload_document",
                            {"title": "MCP 설계", "content": "# 설계\n본문", "category": "backend"})
        s = self.structured(body)
        self.assertEqual(s["action"], "CREATED")
        self.assertEqual(s["masked_secrets"], 0)
        doc = Document.objects.get(pk=s["document_id"])
        self.assertEqual(doc.owner, self.user)
        self.assertEqual(doc.project, self.project)
        v = DocumentVersion.objects.get(document=doc, version=1)
        self.assertEqual(v.source, DocumentVersion.Source.MCP_PUSH)

    def test_secrets_are_masked_before_save_and_counted(self):
        content = "키: sk-abcdefghijklmnopqrstuvwxyz\nDB postgres://u:p@host/db"
        _, body = self.call("bordo_upload_document", {"title": "메모", "content": content})
        r = self.result(body)
        s = r["structuredContent"]
        self.assertEqual(s["masked_secrets"], 2)
        self.assertIn("2건", r["content"][0]["text"])
        doc = Document.objects.get(pk=s["document_id"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", doc.content)
        self.assertNotIn("u:p@host", doc.content)
        self.assertNotIn("u:p@host", DocumentVersion.objects.get(document=doc).content)

    def test_same_title_and_content_is_not_duplicated(self):
        _, first = self.call("bordo_upload_document", {"title": "A", "content": "같음"})
        _, second = self.call("bordo_upload_document", {"title": "A", "content": "같음"})
        self.assertEqual(self.structured(second)["action"], "UNCHANGED")
        self.assertEqual(self.structured(second)["document_id"],
                         self.structured(first)["document_id"])
        self.assertEqual(Document.objects.filter(title="A").count(), 1)

    def test_requires_title_and_content(self):
        for args in ({"title": "A"}, {"content": "x"}, {"title": "A", "content": "  "},
                     {"title": "A", "content": "x", "visibility": "hidden"}):
            _, body = self.call("bordo_upload_document", args)
            self.assertTrue(self.result(body)["isError"], args)


class CompleteWorkTest(McpTestBase):

    def _work(self, title="MCP 연동", **kw):
        kw.setdefault("progress", 40)
        return WorkItem.objects.create(project=self.project, owner=self.user, title=title, **kw)

    def test_complete_by_id(self):
        w = self._work()
        _, body = self.call("bordo_complete_work", {"work_item_id": str(w.id), "note": "끝"})
        s = self.structured(body)
        self.assertEqual(s["action"], "COMPLETED")
        w.refresh_from_db()
        self.assertEqual((w.status, w.progress, w.source), ("DONE", 100, Source.MCP))
        ev = ActivityEvent.objects.get(target_id=w.id, kind="work.completed")
        self.assertEqual(ev.detail["note"], "끝")

    def test_complete_by_title_uses_default_project(self):
        self._work()
        _, body = self.call("bordo_complete_work", {"title": "MCP 연동"})
        s = self.structured(body)
        self.assertEqual(s["action"], "COMPLETED")
        self.assertEqual(s["project"]["resolved_by"], "default")

    def test_already_done_is_success_not_error(self):
        w = self._work(status="DONE", progress=100)
        _, body = self.call("bordo_complete_work", {"work_item_id": str(w.id)})
        r = self.result(body)
        self.assertFalse(r["isError"])
        self.assertEqual(r["structuredContent"]["action"], "ALREADY_DONE")

    def test_neither_id_nor_title_is_tool_error(self):
        _, body = self.call("bordo_complete_work", {})
        r = self.result(body)
        self.assertTrue(r["isError"])
        self.assertIn("work_item_id", r["content"][0]["text"])

    def test_cannot_complete_someone_elses_work(self):
        w = WorkItem.objects.create(project=self.project2, owner=self.other, title="남의 일")
        _, body = self.call("bordo_complete_work", {"work_item_id": str(w.id)})
        self.assertTrue(self.result(body)["isError"])
        w.refresh_from_db()
        self.assertNotEqual(w.status, "DONE")

    def test_unknown_title(self):
        _, body = self.call("bordo_complete_work", {"title": "없는 일"})
        r = self.result(body)
        self.assertTrue(r["isError"])
        self.assertEqual(r["structuredContent"]["code"], "STATE_NOT_FOUND")

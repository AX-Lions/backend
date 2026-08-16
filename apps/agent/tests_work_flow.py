"""
작업 플로우 화살표 생성기.

여기서 보는 것은 셋입니다.

    1. 의미 있는 변화만 남는가 — 전부 그리면 화살표에 묻혀 아무것도 안 보인다
    2. 비공개가 새지 않는가 — 플로우는 팀 관점 화면이라 남에게 그대로 보인다
    3. 화살표를 못 그려도 원래 저장은 되는가 — 작업 생성이 실패하면 안 된다
"""
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.meetings.models import FlowCategory, FlowContentType, FlowEdge, FlowSource
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from apps.states.models import WorkItem


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="w1@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.mate = User.objects.create_user(email="w2@bordo.dev", password="x" * 10,
                                            name="최비성")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def _edges(self, ctype=None):
        qs = FlowEdge.objects.filter(category=FlowCategory.WORK)
        return list(qs.filter(content_type=ctype) if ctype else qs)

    def _work(self, **kw):
        return WorkItem.objects.create(
            project=self.project, owner=self.me,
            title=kw.pop("title", "로그인 API 구현"),
            summary="", status=kw.pop("status", "TODO"), **kw)


class WorkItemEdgeTest(Base):

    def test_creating_work_draws_an_arrow(self):
        self._work()
        edges = self._edges(FlowContentType.WORK)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].label, "로그인 API 구현")
        self.assertIsNone(edges[0].meeting_id, "작업 엣지에는 회의가 없습니다")

    def test_the_arrow_goes_to_the_rest_of_the_team(self):
        """작업 화살표는 "누가 했다" 를 팀에게 알리는 것입니다."""
        self._work()
        e = self._edges(FlowContentType.WORK)[0]
        self.assertEqual(e.from_node["name"], "서재민")
        self.assertEqual([n["name"] for n in e.to_nodes], ["최비성"])

    def test_status_change_is_a_revision(self):
        item = self._work()
        FlowEdge.objects.all().delete()

        item.status = "IN_PROGRESS"
        item.save()
        self.assertEqual(len(self._edges(FlowContentType.REVISION)), 1)

    def test_progress_only_change_draws_nothing(self):
        """
        1% 씩 올릴 때마다 화살표가 쌓이면 정작 무엇이 끝났는지가 안 보입니다.
        """
        item = self._work()
        FlowEdge.objects.all().delete()

        item.progress = 40
        item.save()
        self.assertEqual(self._edges(), [])

    def test_private_work_is_not_drawn(self):
        """
        플로우는 팀 관점 화면이라 남에게 그대로 보입니다. 제목만 스쳐도
        비공개로 둔 뜻이 사라집니다.
        """
        self._work(visibility="private", title="아직 말 못 할 것")
        self.assertEqual(self._edges(), [])

    def test_a_solo_project_draws_nothing(self):
        """
        화살표는 흐름입니다. 받는 쪽이 없으면 흐름이 아닙니다.
        """
        ProjectMember.objects.filter(project=self.project, user=self.mate).delete()
        self._work()
        self.assertEqual(self._edges(), [])

    def test_a_failed_arrow_does_not_block_the_save(self):
        """
        화살표 하나 때문에 작업 생성이 실패하면 안 됩니다.
        """
        with patch("apps.meetings.models.FlowEdge.objects.create",
                   side_effect=RuntimeError("디스크 꽉 참")):
            item = self._work()
        self.assertIsNotNone(item.pk)
        self.assertEqual(WorkItem.objects.count(), 1)


class DocumentEdgeTest(Base):

    def _doc(self, **kw):
        from apps.documents.models import Document
        return Document.objects.create(
            project=self.project, owner=self.me,
            title=kw.pop("title", "API 명세서"), **kw)

    def test_creating_a_document_draws_work(self):
        self._doc()
        self.assertEqual(len(self._edges(FlowContentType.WORK)), 1)

    def test_editing_a_document_draws_revision(self):
        doc = self._doc()
        FlowEdge.objects.all().delete()
        doc.title = "API 명세서 v2"
        doc.save()
        self.assertEqual(len(self._edges(FlowContentType.REVISION)), 1)

    def test_delivered_document_draws_share(self):
        """전달은 `delivery_context` 가 채워질 때입니다."""
        self._doc(delivery_context=[{"to": "최비성", "url": "https://github.com/x"}])
        self.assertEqual(len(self._edges(FlowContentType.SHARE)), 1)

    def test_source_is_read_from_the_link(self):
        """
        화면의 `필터링 > 출처` 가 Github · Figma · Notion 입니다.
        """
        self._doc(delivery_context=[{"url": "https://www.figma.com/design/abc"}])
        self.assertEqual(self._edges(FlowContentType.SHARE)[0].source,
                         FlowSource.FIGMA)

    def test_unknown_link_leaves_the_source_empty(self):
        """억지로 채우면 필터가 거짓말을 합니다."""
        self._doc(delivery_context=[{"url": "https://example.com/x"}])
        self.assertEqual(self._edges(FlowContentType.SHARE)[0].source, "")

    def test_private_document_is_not_drawn(self):
        self._doc(visibility="private")
        self.assertEqual(self._edges(), [])


class ChatFeedbackEdgeTest(Base):

    def _room(self):
        from apps.chat.models import ChatRoom, RoomType
        return ChatRoom.objects.create(type=RoomType.PROJECT, project=self.project,
                                       created_by=self.me)

    def _msg(self, **kw):
        from apps.chat.models import ChatMessage
        return ChatMessage.objects.create(
            room=kw.pop("room", None) or self._room(),
            sender=self.me, sender_name="서재민",
            body=kw.pop("body", "이 부분은 다시 봐야 할 것 같아요"), **kw)

    def test_only_important_messages_become_feedback(self):
        """
        모든 메시지를 그리면 플로우가 대화 로그가 됩니다. 회의 플로우에서
        검색 호출을 안 그린 것과 같은 이유입니다.
        """
        room = self._room()
        self._msg(room=room)
        self.assertEqual(self._edges(), [])

        self._msg(room=room, is_important=True)
        self.assertEqual(len(self._edges(FlowContentType.FEEDBACK)), 1)

    def test_agent_messages_are_skipped(self):
        """대리인 발언은 회의 플로우가 이미 그립니다."""
        self._msg(is_important=True, is_agent=True)
        self.assertEqual(self._edges(), [])

"""
불참 등록과 그 주변.

화면이 기다리는 모양과 어긋나 있던 자리들입니다. 어긋나면 400 이나 500 이
아니라 **버튼이 안 뜨거나 고른 것이 조용히 사라지는** 식으로 나타나서,
"이건 이렇게 와야 한다" 를 여기 적어 둡니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.documents.models import Document, Visibility
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

from .models import (Attendance, FlowCategory, Meeting, MeetingParticipant,
                     MeetingStatus)


def _today_at_noon():
    """
    오늘의 UTC 정오.

    `now() + 1시간` 으로 잡으면 실행 시각이 23시대일 때 내일로 넘어가
    `today_schedule` 에서 빠집니다 — 밤에 돌리면 실패하는 테스트가 됩니다.
    """
    return timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)


class DelegationTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="유수인", timezone="Asia/Seoul")
        cls.mate = User.objects.create_user(email="mate@bordo.dev", password="x" * 10,
                                            name="서재민")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.mate, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                             created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.mate)

        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="프로젝트", title="정기 회의",
            scheduled_at=_today_at_noon(), duration_min=60,
            status=MeetingStatus.CONFIRMED, created_by=cls.me)
        MeetingParticipant.objects.create(meeting=cls.meeting, user=cls.me,
                                          user_name="유수인")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _delegate(self, **body):
        return self.client.post(f"/api/v1/meetings/{self.meeting.id}/delegate",
                                body, format="json")

    def _today(self):
        rows = self.client.get("/api/v1/home").data["today_schedule"]
        return next(r for r in rows if r["meeting_id"] == str(self.meeting.id))

    # ── 홈이 버튼을 그릴 수 있는가
    def test_home_schedule_carries_delegation(self):
        """
        이게 없으면 홈의 `회의에 참여하지 않아요` 버튼이 **절대 안 뜹니다.**

        화면은 이 값의 유무로 불참 등록 버튼과 회의 링크를 가릅니다. 불참 등록이
        이 서비스의 시작점이라 진입로가 통째로 사라집니다.
        """
        self.assertEqual(self._today()["delegation"],
                         {"delegated": False, "prompt": "", "sources": None})

    def test_home_hides_the_button_for_non_participants(self):
        """
        참석자가 아니면 `null` 입니다.

        빈 객체를 주면 남의 회의에도 버튼이 떠서, 눌러 봐야 `참석자가 아닙니다`
        로 막힙니다.
        """
        self.client.force_authenticate(self.mate)
        self.assertIsNone(self._today()["delegation"])

    def test_home_reflects_what_was_saved(self):
        self._delegate(enabled=True, sources=["work"], prompt="일정은 확정하지 마세요")
        self.assertEqual(self._today()["delegation"],
                         {"delegated": True, "prompt": "일정은 확정하지 마세요",
                          "sources": ["work"]})

    # ── 고른 자료 범위
    def test_sources_round_trip(self):
        """
        응답에 그대로 실려야 합니다.

        화면이 이 응답으로 목록을 갱신하는데 키가 없으면 방금 고른 범위가
        `undefined` 로 덮여, 팝업을 다시 열었을 때 전부 켜진 것으로 보입니다.
        """
        r = self._delegate(enabled=True, sources=["thought", "work"], prompt="")
        self.assertEqual(r.status_code, 200)
        # 화면 순서로 맞춰 돌려줍니다. 고른 순서대로 주면 같은 조합인데
        # 응답 순서가 매번 달라 칸 순서가 흔들립니다.
        self.assertEqual(r.data["sources"], ["work", "thought"])

    def test_empty_sources_is_a_real_choice(self):
        """
        `[]` 은 오류가 아닙니다.

        "대리인은 보내되 내 기록은 쓰지 마라" 는 성립하는 선택입니다. 막으면
        회의 발언만 듣고 답하거나 유보하는 쓰임이 사라집니다.
        """
        r = self._delegate(enabled=True, sources=[], prompt="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["sources"], [])

    def test_missing_sources_key_keeps_the_previous_choice(self):
        """
        키가 없으면 직전 값을 둡니다.

        빈 배열로 덮으면 Discord 등 다른 경로로 켜 둔 범위가 저장 한 번에
        전부 꺼집니다.
        """
        self._delegate(enabled=True, sources=["plan"], prompt="")
        r = self._delegate(enabled=False, prompt="")
        self.assertEqual(r.data["sources"], ["plan"])

    def test_unknown_source_is_rejected(self):
        r = self._delegate(enabled=True, sources=["secret"], prompt="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(r.data["error"]["details"]["invalid"], ["secret"])

    def test_delegation_marks_attendance(self):
        self._delegate(enabled=True, sources=["work"], prompt="")
        p = MeetingParticipant.objects.get(meeting=self.meeting, user=self.me)
        self.assertEqual(p.attendance, Attendance.DELEGATED)


class WorkIndexTest(TestCase):
    """
    작업 모드 좌측 인덱스.

    경로에 회의 id 가 붙어 있지만 **작업 엣지에는 회의가 없습니다.** 회의로
    좁히면 조건에 맞는 행이 하나도 없어 이 목록은 언제나 빈 배열이었습니다.
    """

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                          name="유수인")
        cls.mate = User.objects.create_user(email="w2@bordo.dev", password="x" * 10,
                                            name="서재민")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.mate, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                             created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.mate)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="프로젝트", title="회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        MeetingParticipant.objects.create(meeting=cls.meeting, user=cls.me,
                                          user_name="유수인")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _indexes(self, **params):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/indexes",
                            {"category": "WORK", **params})
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_document_shows_up_in_the_work_index(self):
        doc = Document.objects.create(project=self.project, title="API 명세",
                                      owner=self.me)
        body = self._indexes()
        self.assertEqual([r["label"] for r in body["results"]], ["API 명세"])
        # 인덱스를 누르면 이 id 로 판의 화살표를 강조합니다. 비어 있으면
        # 목록은 뜨는데 눌러도 아무 일이 없습니다.
        self.assertTrue(body["results"][0]["related_edge_ids"])
        self.assertEqual(str(doc.id), body["results"][0]["id"])

    def test_private_document_stays_hidden(self):
        """
        작업 플로우는 팀 관점 화면이라 제목만 스쳐도 비공개로 둔 뜻이 사라집니다.
        """
        Document.objects.create(project=self.project, title="비공개 메모",
                                owner=self.mate, visibility=Visibility.PRIVATE)
        self.assertEqual(self._indexes()["count"], 0)

    def test_index_period_matches_the_board(self):
        """
        인덱스와 캔버스가 다른 구간을 보면, 인덱스의 문서를 눌러도 강조될
        화살표가 판에 없습니다.
        """
        doc = Document.objects.create(project=self.project, title="지난달 문서",
                                      owner=self.me)
        old = timezone.now() - timedelta(days=30)
        doc.flow_edges.update(occurred_at=old)
        self.assertEqual(self._indexes()["count"], 0)

        # 기간을 넓히면 인덱스에도 판에도 함께 나옵니다.
        params = {"from": (old - timedelta(days=1)).isoformat(),
                  "to": timezone.now().isoformat()}
        self.assertEqual(self._indexes(**params)["count"], 1)
        flow = self.client.get(f"/api/v1/projects/{self.project.id}/flow", params).data
        self.assertEqual(flow["category"], FlowCategory.WORK)
        self.assertTrue(flow["arrows"])


class BriefingReadTest(TestCase):
    """
    브리핑 조회가 읽음까지 찍던 문제.

    플로우 화면은 브리핑 패널을 열든 말든 회의를 열 때 브리핑을 부릅니다.
    그래서 회의 화면에 잠깐 들른 것만으로 홈의 `Bordo 브리핑 보러가기` 가
    사라졌습니다 — 사용자는 읽은 적이 없는데 읽은 것이 됩니다.
    """

    @classmethod
    def setUpTestData(cls):
        from .models import AiBriefing

        cls.me = User.objects.create_user(email="b@bordo.dev", password="x" * 10,
                                          name="유수인")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                         created_by=cls.me)
        ProjectMember.objects.create(project=project, user=cls.me)
        cls.meeting = Meeting.objects.create(
            project=project, project_name="프로젝트", title="회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        cls.briefing = AiBriefing.objects.create(meeting=cls.meeting, user=cls.me,
                                                 narrative="정리해 두었습니다.")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _pending(self):
        return self.client.get("/api/v1/home").data["briefing_pending"]["exists"]

    def test_mark_read_false_keeps_the_home_button(self):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing",
                            {"mark_read": "false"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._pending())

    def test_default_still_marks_read(self):
        """
        기본을 끄면 이 값을 안 보내는 쪽에서는 브리핑이 영영 안 읽힌 상태로
        남아 홈 팝업이 매번 뜹니다.
        """
        self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing")
        self.assertFalse(self._pending())

"""
회의별 자료 범위.

## 저장만 확인하면 부족합니다

값이 DB 에 들어갔는데 검색이 그것을 안 보면, 사용자는 `생각` 을 꺼 뒀는데
대리인이 회의에서 그 내용을 말합니다. **되돌릴 수 없는 종류의 실패**라
검색 결과까지 봅니다.

`null` 과 `[]` 를 가르는 것도 여기서 확인합니다. 둘을 같게 다루면 **전부 끈
사람의 대리인이 모든 자료를 보게** 됩니다 — 정반대로 동작하는 셈입니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.services.skills import SkillContext
from apps.agent.services.skills.search_records import SearchRecordsSkill
from apps.meetings.models import Meeting, MeetingParticipant, MeetingStatus
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from apps.states.models import PlanItem, ThoughtItem, WorkItem


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="s1@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            created_by=cls.me, status=MeetingStatus.CONFIRMED,
            # 홈의 `오늘 일정` 에 걸리려면 오늘이어야 합니다.
            scheduled_at=timezone.now() + timezone.timedelta(minutes=30))
        cls.part = MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.me, user_name="서재민")

        WorkItem.objects.create(project=cls.project, owner=cls.me,
                                title="로그인 API 구현")
        PlanItem.objects.create(project=cls.project, owner=cls.me,
                                title="로그인 QA 계획")
        ThoughtItem.objects.create(project=cls.project, owner=cls.me,
                                   topic="로그인 구조가 불안하다",
                                   content="토큰 만료 처리가 걸린다")

    def _search(self, allowed):
        ctx = SkillContext(principal_id=str(self.me.id),
                           project_id=str(self.project.id),
                           meeting_id=str(self.meeting.id),
                           allowed_sources=allowed)
        result = SearchRecordsSkill().run({"query": "로그인"}, ctx)
        return result, {e["source_type"] for e in result.evidence}


class ScopeTest(Base):

    def test_no_choice_means_everything(self):
        """고른 적 없으면(`None`) 전부 봅니다."""
        _, kinds = self._search(None)
        self.assertEqual(kinds, {"work", "plan", "thought"})

    def test_only_the_chosen_kind_is_searched(self):
        _, kinds = self._search(["work"])
        self.assertEqual(kinds, {"work"})

    def test_an_empty_list_blocks_everything(self):
        """
        `[]` 는 **아무것도 안 쓴다**는 뜻입니다.

        `None` 과 같게 다루면 전부 끈 사람의 대리인이 모든 자료를 봅니다.
        """
        result, kinds = self._search([])
        self.assertEqual(kinds, set())
        # 0건과 다릅니다. 찾을 자리 자체가 없다는 것을 모델이 알아야
        # 검색어만 바꿔 가며 헛돌지 않습니다.
        self.assertIn("근거로 쓰지 않기로", result.message)

    def test_the_model_cannot_widen_the_scope(self):
        """
        모델이 `kinds` 로 더 넓게 요청해도 늘어나지 않습니다.

        범위 설정은 넓히는 장치가 아니라 좁히는 장치입니다. 검색어나 kinds 를
        바꿔 가며 다시 불러도 꺼 둔 종류는 나오지 않아야 합니다.
        """
        ctx = SkillContext(principal_id=str(self.me.id),
                           project_id=str(self.project.id),
                           allowed_sources=["work"])
        result = SearchRecordsSkill().run(
            {"query": "로그인", "kinds": ["work", "plan", "thought", "document"]}, ctx)

        self.assertEqual({e["source_type"] for e in result.evidence}, {"work"})


class DelegateApiTest(Base):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _post(self, body):
        return self.client.post(f"/api/v1/meetings/{self.meeting.id}/delegate",
                                body, format="json")

    def test_sources_are_saved(self):
        res = self._post({"enabled": True, "sources": ["work", "document"]})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["sources"], ["work", "document"])
        self.part.refresh_from_db()
        self.assertEqual(self.part.allowed_sources, ["work", "document"])

    def test_an_empty_list_is_kept_as_is(self):
        self._post({"enabled": True, "sources": []})

        self.part.refresh_from_db()
        self.assertEqual(self.part.allowed_sources, [])
        # 제한 없음으로 되돌아가면 안 됩니다.
        self.assertEqual(self.part.source_scope, [])

    def test_omitting_sources_keeps_the_current_value(self):
        self._post({"enabled": True, "sources": ["plan"]})
        self._post({"enabled": True, "prompt": "일정은 확인받아 주세요"})

        self.part.refresh_from_db()
        self.assertEqual(self.part.allowed_sources, ["plan"])

    def test_an_unknown_kind_is_refused(self):
        res = self._post({"enabled": True, "sources": ["work", "salary"]})

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")
        self.part.refresh_from_db()
        self.assertIsNone(self.part.allowed_sources)

    def test_duplicates_are_collapsed(self):
        res = self._post({"enabled": True, "sources": ["work", "work", "plan"]})

        self.assertEqual(res.json()["sources"], ["work", "plan"])


class HomeTest(Base):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_home_carries_my_delegation_state(self):
        """
        버튼 문구가 `회의에 참여하지 않아요` / `대리 참석 중` 으로 갈립니다.
        회의마다 따로 부르면 세 줄짜리 목록에 요청이 세 번 더 나갑니다.
        """
        self.client.post(f"/api/v1/meetings/{self.meeting.id}/delegate",
                         {"enabled": True, "sources": ["work"]}, format="json")

        rows = self.client.get("/api/v1/home").json()["today_schedule"]
        row = next(r for r in rows if r["meeting_id"] == str(self.meeting.id))

        self.assertTrue(row["delegation"]["delegated"])
        self.assertEqual(row["delegation"]["sources"], ["work"])

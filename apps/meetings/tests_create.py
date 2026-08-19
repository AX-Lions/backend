"""
회의 만들기 (`POST /projects/{id}/meetings`).

화면(`NewMeetingDialog`)에 참석자 피커가 있는데 서버가 `participant_ids` 를 안
읽어, 골라도 프로젝트 전원이 들어갔습니다. 옆에 나란히 있는 일정 만들기는 이미
받고 있어 똑같이 생긴 다이얼로그 둘이 다르게 동작했습니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import Meeting
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class CreateMeetingTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="최비성")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="서재민")
        cls.outsider = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                                name="남의 사람")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def post(self, **kw):
        body = {"title": "정기 회의",
                "scheduled_at": timezone.now().isoformat()}
        body.update(kw)
        return self.client.post(f"/api/v1/projects/{self.project.id}/meetings",
                                body, format="json")

    def test_all_project_members_when_not_specified(self):
        """이 키를 모르는 클라이언트가 참석자 0명 회의를 만들면 안 됩니다."""
        r = self.post()
        self.assertEqual(r.status_code, 201)
        m = Meeting.objects.get()
        self.assertEqual(m.participants.count(), 2)

    def test_only_the_chosen_people(self):
        r = self.post(participant_ids=[str(self.me.id)])
        self.assertEqual(r.status_code, 201)
        m = Meeting.objects.get()
        self.assertEqual(list(m.participants.values_list("user_id", flat=True)),
                         [self.me.id])

    def test_outsiders_are_rejected(self):
        """조용히 빼면 화면은 넣었다고 보이는데 회의에는 없습니다."""
        r = self.post(participant_ids=[str(self.me.id), str(self.outsider.id)])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["error"]["code"], "PROJECT_ACCESS_DENIED")
        self.assertFalse(Meeting.objects.exists())

    def test_bad_scheduled_at_is_400_not_500(self):
        """문자열을 그대로 넣으면 DB 에는 들어가고 직렬화에서 터졌습니다."""
        r = self.post(scheduled_at="내일 오후 세시")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["error"]["code"], "VALIDATION_ERROR")

    def test_scheduled_at_is_required(self):
        r = self.post(scheduled_at=None)
        self.assertEqual(r.status_code, 400)

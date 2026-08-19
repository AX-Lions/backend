"""
초대 코드로 들어온 사람이 어느 프로젝트에 함께 들어가는가 (이슈 #83).

전에는 `TeamMember` 만 만들어서, 초대로 들어온 사람은 팀에는 있는데 홈이
비어 있었습니다. 반대로 전부 넣으면 일부만 골라 만든 프로젝트에 일부러 빼 둔
사람이 들어갑니다. 권한을 넓히는 쪽으로 잘못되면 되돌려도 이미 본 것은 못
지웁니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orgs.models import (InviteCode, Project, ProjectMember, Team, TeamMember,
                              TeamRole)


class JoinTeamTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                              name="유수인")
        self.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                             name="최비성")
        self.newbie = User.objects.create_user(email="n@bordo.dev", password="x" * 10,
                                               name="강다은")
        self.team = Team.objects.create(name="AX Lions", created_by=self.owner)
        for u in (self.owner, self.mate):
            TeamMember.objects.create(team=self.team, user=u, team_role=TeamRole.MEMBER)

        self.code = InviteCode.objects.create(
            code="BRD-A1B2-C3D4", team=self.team, default_role=TeamRole.MEMBER,
            max_uses=10, expires_at=timezone.now() + timedelta(days=7))

        self.client = APIClient()
        self.client.force_authenticate(self.newbie)

    def project(self, name, members):
        p = Project.objects.create(team=self.team, team_name=self.team.name,
                                   name=name, created_by=self.owner)
        for u in members:
            ProjectMember.objects.create(project=p, user=u)
        return p

    def join(self):
        r = self.client.post("/api/v1/teams/join", {"code": self.code.code},
                             format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def members_of(self, project):
        return set(ProjectMember.objects.filter(project=project)
                   .values_list("user_id", flat=True))

    def test_joins_a_project_that_holds_the_whole_team(self):
        """팀에는 들어왔는데 홈이 비어 있으면 무엇을 하는 팀인지 알 수 없습니다."""
        whole = self.project("전원 프로젝트", [self.owner, self.mate])
        self.join()
        self.assertIn(self.newbie.id, self.members_of(whole))

    def test_does_not_join_a_partial_project(self):
        """일부러 빼 둔 사람이 들어가면 되돌려도 이미 본 것은 못 지웁니다."""
        partial = self.project("일부 프로젝트", [self.owner])
        self.join()
        self.assertNotIn(self.newbie.id, self.members_of(partial))

    def test_response_names_the_projects(self):
        """개수만 주면 어디에 들어갔는지 보려고 목록을 한 번 더 불러야 합니다."""
        self.project("전원 프로젝트", [self.owner, self.mate])
        self.project("일부 프로젝트", [self.owner])
        body = self.join()
        self.assertEqual([p["name"] for p in body["joined_projects"]],
                         ["전원 프로젝트"])

    def test_first_member_joins_nothing(self):
        """비교할 기준이 없습니다. 빈 팀의 프로젝트에 아무나 들어가면 안 됩니다."""
        TeamMember.objects.filter(team=self.team).delete()
        lonely = self.project("주인 없는 프로젝트", [])
        self.assertEqual(self.join()["joined_projects"], [])
        self.assertNotIn(self.newbie.id, self.members_of(lonely))

    def test_team_membership_is_still_created(self):
        self.join()
        self.assertTrue(TeamMember.objects.filter(team=self.team,
                                                  user=self.newbie).exists())

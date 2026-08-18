"""섹션 제목·한 줄 요약도 가려지는가 (이슈 #78)."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.documents.models import Document
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

KEY = "sk-abcdefghijklmnop1234"


class SectionMaskingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="d@b.dev", name="서재민")
        team = Team.objects.create(name="팀", created_by=self.user)
        TeamMember.objects.create(team=team, user=self.user, team_role="OWNER")
        self.project = Project.objects.create(team=team, team_name=team.name,
                                              name="프로젝트", created_by=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create(self, sections):
        return self.client.post(f"/api/v1/projects/{self.project.id}/documents",
                                {"title": "설계", "sections": sections}, format="json")

    def test_제목에_넣은_키가_저장되지_않는다(self):
        r = self._create([{"heading": f"배포 키 {KEY}", "one_line_summary": "", "body": ""}])
        self.assertEqual(r.status_code, 201)
        doc = Document.objects.get(pk=r.json()["id"])
        self.assertNotIn(KEY, doc.sections[0]["heading"])
        self.assertIn("sk-***", doc.sections[0]["heading"])

    def test_한줄요약에_넣은_키가_저장되지_않는다(self):
        r = self._create([{"heading": "", "one_line_summary": f"키는 {KEY}", "body": ""}])
        doc = Document.objects.get(pk=r.json()["id"])
        self.assertNotIn(KEY, doc.sections[0]["one_line_summary"])

    def test_조회_응답에도_새지_않는다(self):
        r = self._create([{"heading": f"{KEY}", "one_line_summary": f"{KEY}", "body": f"{KEY}"}])
        got = self.client.get(f"/api/v1/documents/{r.json()['id']}")
        self.assertNotIn(KEY, got.content.decode(),
                         "검색 결과와 대리인 발언에 그대로 실릴 수 있다")

    def test_가린_개수에_잡힌다(self):
        r = self._create([{"heading": KEY, "one_line_summary": KEY, "body": KEY}])
        doc = Document.objects.get(pk=r.json()["id"])
        self.assertEqual(doc.masked_secrets, 3,
                         "가려졌다고 표시되는데 실제로는 안 가려진 것이 가장 나쁘다")

    def test_평범한_섹션은_그대로다(self):
        r = self._create([{"heading": "개요", "one_line_summary": "한 줄", "body": "본문"}])
        doc = Document.objects.get(pk=r.json()["id"])
        self.assertEqual(doc.sections[0],
                         {"heading": "개요", "one_line_summary": "한 줄", "body": "본문"})
        self.assertEqual(doc.masked_secrets, 0)

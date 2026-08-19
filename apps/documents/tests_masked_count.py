"""
`masked_secrets` 가 언제 다시 세지는가 (이슈 #7).

보안 구멍은 아닙니다 — 본문은 계속 가려져 있습니다. 화면에 `지운 비밀키 N건`
을 띄우는데 메타데이터만 고쳐도 0 이 되어, 사용자가 **마스킹이 풀린 것으로**
읽는 것이 문제입니다. 화면만 거짓말하는 상태는 눈으로 못 찾습니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.documents.models import Document
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

KEY = "sk-abcdefghijklmnop1234"
DSN = "postgres://admin:pw@db.internal:5432/bordo"


class MaskedCountTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(email="d@b.dev", name="서재민")
        team = Team.objects.create(name="팀", created_by=self.user)
        TeamMember.objects.create(team=team, user=self.user, team_role="OWNER")
        self.project = Project.objects.create(team=team, team_name=team.name,
                                              name="프로젝트", created_by=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        r = self.client.post(f"/api/v1/projects/{self.project.id}/documents",
                             {"title": "배포 메모",
                              "content": f"키는 {KEY} 이고 DB 는 {DSN} 입니다."},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.doc_id = r.json()["id"]
        self.assertEqual(Document.objects.get(pk=self.doc_id).masked_secrets, 2)

    def patch(self, body):
        r = self.client.patch(f"/api/v1/documents/{self.doc_id}", body, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return Document.objects.get(pk=self.doc_id)

    def test_제목만_고치면_건수가_그대로다(self):
        self.assertEqual(self.patch({"title": "배포 메모 v2"}).masked_secrets, 2)

    def test_공개범위만_고쳐도_그대로다(self):
        self.assertEqual(self.patch({"visibility": "private"}).masked_secrets, 2)

    def test_본문이_새로_오면_다시_센다(self):
        doc = self.patch({"content": f"이제 키는 {KEY} 하나뿐입니다."})
        self.assertEqual(doc.masked_secrets, 1)
        self.assertNotIn(KEY, doc.content)

    def test_비밀키를_뺀_본문으로_바꾸면_0_이_된다(self):
        """실제로 없어진 것과 안 세어 본 것은 다릅니다."""
        self.assertEqual(self.patch({"content": "이제 키가 없습니다."}).masked_secrets, 0)

    def test_제목만_고쳐도_본문은_계속_가려져_있다(self):
        doc = self.patch({"title": "무관한 수정"})
        self.assertNotIn(KEY, doc.content)
        self.assertIn("sk-***", doc.content)

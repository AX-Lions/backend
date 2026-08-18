"""MCP 테스트 공통 — 사용자 · 팀 · 프로젝트 · 토큰."""
import json

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.orgs.models import Project, ProjectMember, RecentProject, Team, TeamMember, TeamRole

from .models import McpToken


class McpTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                            name="재민")
        cls.other = User.objects.create_user(email="other@bordo.dev", password="x" * 10,
                                             name="남")
        cls.team = Team.objects.create(name="Bordo", created_by=cls.user)
        TeamMember.objects.create(team=cls.team, user=cls.user, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=cls.team, user=cls.other, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, name="백엔드", created_by=cls.user)
        cls.project2 = Project.objects.create(team=cls.team, name="봇", created_by=cls.user)
        for p in (cls.project, cls.project2):
            ProjectMember.objects.create(project=p, user=cls.user)
        ProjectMember.objects.create(project=cls.project2, user=cls.other)
        RecentProject.objects.create(user=cls.user, project=cls.project,
                                     opened_at=timezone.now())

    def setUp(self):
        self.token_row, self.token = McpToken.issue(self.user)

    # ── 호출 도우미 ─────────────────────────────────────────
    def rpc(self, body, *, token=None, headers=None, raw=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"}
        h.update(headers or {})
        data = raw if raw is not None else json.dumps(body)
        return self.client.post("/mcp", data=data, content_type="application/json", **h)

    def call(self, name, arguments=None, *, id_=1, modern=False, headers=None, token=None):
        params = {"name": name, "arguments": arguments or {}}
        h = dict(headers or {})
        if modern:
            params["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
            h.setdefault("HTTP_MCP_PROTOCOL_VERSION", "2026-07-28")
            h.setdefault("HTTP_MCP_METHOD", "tools/call")
            h.setdefault("HTTP_MCP_NAME", name)
        res = self.rpc({"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": params},
                       headers=h, token=token)
        return res, (res.json() if res.content else None)

    @staticmethod
    def result(body):
        return body["result"]

    @staticmethod
    def structured(body):
        return body["result"]["structuredContent"]

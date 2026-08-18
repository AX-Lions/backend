"""
토큰 발급·검증.

막아야 하는 것: 원문이 서버에 남는 것, 폐기된 토큰이 통하는 것, 남의 토큰으로
남의 프로젝트에 쓰는 것.
"""
from django.test import override_settings
from rest_framework.test import APIClient

from .models import PREFIX, McpToken
from .tests_base import McpTestBase


class TokenTest(McpTestBase):

    def test_raw_token_is_not_stored(self):
        self.assertTrue(self.token.startswith(PREFIX))
        self.assertNotEqual(self.token_row.token_hash, self.token)
        self.assertNotIn(self.token, self.token_row.token_hash)
        self.assertEqual(self.token_row.prefix, self.token[:12])

    def test_reissue_revokes_previous(self):
        """사용자당 활성 1개 — 재발급이 곧 폐기입니다."""
        old = self.token
        _, new = McpToken.issue(self.user)
        self.assertIsNone(McpToken.authenticate(old))
        self.assertIsNotNone(McpToken.authenticate(new))
        self.assertEqual(McpToken.objects.filter(user=self.user, revoked_at__isnull=True).count(), 1)

    def test_authenticate_rejects_garbage(self):
        for bad in ("", "brd_", "Bearer x", "dpt_abc", self.token + "x", self.token[:-1]):
            self.assertIsNone(McpToken.authenticate(bad), bad)

    def test_missing_or_wrong_token_is_401(self):
        res = self.client.post("/mcp", data="{}", content_type="application/json")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "AUTH_MCP_TOKEN_INVALID")

        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="brd_nope")
        self.assertEqual(res.status_code, 401)

    def test_401_even_when_body_is_broken(self):
        """인증은 본문을 읽기 전에 결정됩니다."""
        res = self.rpc(None, raw="{not json", token="brd_nope")
        self.assertEqual(res.status_code, 401)

    def test_get_and_delete_are_405(self):
        self.assertEqual(self.client.get("/mcp").status_code, 405)
        self.assertEqual(self.client.delete("/mcp").status_code, 405)

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False,
                       CORS_ALLOWED_ORIGINS=["https://app.bordo.dev"])
    def test_foreign_origin_is_403(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"HTTP_ORIGIN": "https://evil.example"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "MCP_ORIGIN_FORBIDDEN")
        ok = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      headers={"HTTP_ORIGIN": "https://app.bordo.dev"})
        self.assertEqual(ok.status_code, 200)

    def test_last_used_is_touched(self):
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.token_row.refresh_from_db()
        self.assertIsNotNone(self.token_row.last_used_at)


class TokenApiTest(McpTestBase):

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_issue_returns_raw_once_and_setup_command(self):
        res = self.api.post("/api/v1/me/mcp-token")
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertTrue(body["token"].startswith(PREFIX))
        self.assertIn(body["token"], body["setup_command"])
        self.assertIn("/mcp", body["setup_command"])
        self.assertIn("issued_at", body)
        # 이전 토큰(setUp 것)은 폐기됨
        self.assertIsNone(McpToken.authenticate(self.token))

    def test_me_exposes_issued_at_and_revoke_clears_it(self):
        me = self.api.get("/api/v1/users/me").json()
        self.assertIsNotNone(me["mcp_token_issued_at"])
        self.assertEqual(self.api.delete("/api/v1/me/mcp-token").status_code, 204)
        me = self.api.get("/api/v1/users/me").json()
        self.assertIsNone(me["mcp_token_issued_at"])
        self.assertIsNone(McpToken.authenticate(self.token))

    def test_token_api_requires_login(self):
        self.assertEqual(APIClient().post("/api/v1/me/mcp-token").status_code, 401)

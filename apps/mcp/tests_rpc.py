"""
JSON-RPC 껍데기 — dual-era 양쪽 · 헤더 검증 · 오류 매핑.

핵심은 **legacy 클라이언트도, modern 클라이언트도 연결이 되는가**입니다.
"""
from .rpc import SUPPORTED_VERSIONS
from .tests_base import McpTestBase

MODERN = "2026-07-28"
META = {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN}}


class LegacyTest(McpTestBase):

    def test_initialize_handshake(self):
        res = self.rpc({"jsonrpc": "2.0", "id": "init-1", "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25",
                                   "clientInfo": {"name": "claude-code", "version": "1"}}})
        self.assertEqual(res.status_code, 200)
        r = res.json()["result"]
        self.assertEqual(r["protocolVersion"], "2025-11-25")
        self.assertEqual(r["serverInfo"]["name"], "bordo")
        self.assertIn("tools", r["capabilities"])
        # 프로젝트 목록과 기본값 안내가 instructions 에 있어야 클라이언트가 project_id 를 압니다
        self.assertIn(str(self.project.id), r["instructions"])
        self.assertIn("생략하면", r["instructions"])

    def test_initialized_notification_is_202_empty(self):
        res = self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.content, b"")

    def test_tools_list_has_three_bordo_tools(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in res.json()["result"]["tools"]}
        self.assertEqual(names, {"bordo_record_work", "bordo_upload_document",
                                 "bordo_complete_work"})
        for t in res.json()["result"]["tools"]:
            self.assertIn("inputSchema", t)          # MCP 키 이름 (parameters 아님)

    def test_unknown_method_is_200_with_32601(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["error"]["code"], -32601)

    def test_ping(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertEqual(res.json()["result"], {})


class ModernTest(McpTestBase):

    def _modern_headers(self, method, name=None):
        h = {"HTTP_MCP_PROTOCOL_VERSION": MODERN, "HTTP_MCP_METHOD": method}
        if name:
            h["HTTP_MCP_NAME"] = name
        return h

    def test_server_discover(self):
        res = self.rpc({"jsonrpc": "2.0", "id": "d-1", "method": "server/discover",
                        "params": dict(META)}, headers=self._modern_headers("server/discover"))
        self.assertEqual(res.status_code, 200)
        r = res.json()["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(r["supportedVersions"], SUPPORTED_VERSIONS)
        self.assertEqual(r["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "bordo")
        self.assertIn(str(self.project.id), r["instructions"])

    def test_header_body_mismatch_is_400_32020(self):
        h = self._modern_headers("tools/list")
        h["HTTP_MCP_PROTOCOL_VERSION"] = "2025-11-25"      # 본문은 2026-07-28
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                        "params": dict(META)}, headers=h)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], -32020)

    def test_tool_name_header_mismatch(self):
        res, body = self.call("bordo_record_work", {"title": "x"}, modern=True,
                              headers={"HTTP_MCP_NAME": "bordo_upload_document"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(body["error"]["code"], -32020)
        self.assertEqual(body["error"]["data"]["header"], "Mcp-Name")

    def test_unsupported_version_is_400_32022_with_supported_list(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2030-01-01"}}},
                       headers={"HTTP_MCP_PROTOCOL_VERSION": "2030-01-01"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], -32022)
        self.assertEqual(res.json()["error"]["data"]["supported"], SUPPORTED_VERSIONS)

    def test_unknown_method_is_404_in_modern(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "resources/list",
                        "params": dict(META)}, headers=self._modern_headers("resources/list"))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"]["code"], -32601)

    def test_tools_call_works_without_headers(self):
        """헤더는 오면 검증하지만 없어도 막지 않습니다."""
        res, body = self.call("bordo_record_work", {"title": "MCP 연동"}, modern=True,
                              headers={"HTTP_MCP_PROTOCOL_VERSION": MODERN})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(body["result"]["isError"])


class ProtocolErrorTest(McpTestBase):

    def test_parse_error(self):
        res = self.rpc(None, raw="{not json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["error"]["code"], -32700)

    def test_invalid_request(self):
        res = self.rpc({"id": 1, "method": "ping"})               # jsonrpc 없음
        self.assertEqual(res.json()["error"]["code"], -32600)
        res = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])   # 배치
        self.assertEqual(res.json()["error"]["code"], -32600)

    def test_unknown_tool_is_32602_with_available_list(self):
        res, body = self.call("bordo_nope")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["error"]["code"], -32602)
        self.assertIn("bordo_record_work", body["error"]["data"]["available"])

    def test_tool_failure_is_isError_not_rpc_error(self):
        """검증 실패는 result.isError — error 로 내면 AI 가 이유를 못 읽습니다."""
        res, body = self.call("bordo_record_work", {"title": "x", "progress": 150})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("error", body)
        r = body["result"]
        self.assertTrue(r["isError"])
        self.assertIn("progress", r["content"][0]["text"])
        self.assertEqual(r["structuredContent"]["code"], "VALIDATION_ERROR")

    def test_server_exception_is_fixed_message(self):
        from unittest import mock
        import logging
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with mock.patch("apps.mcp.tools.write.RecordWork.run", side_effect=RuntimeError("IntegrityError: uq_x")):
            res, body = self.call("bordo_record_work", {"title": "x"})
        r = body["result"]
        self.assertTrue(r["isError"])
        self.assertNotIn("uq_x", r["content"][0]["text"])
        self.assertNotIn("IntegrityError", r["content"][0]["text"])

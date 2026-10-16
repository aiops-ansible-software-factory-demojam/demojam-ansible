"""Exercise the deployed adapter and real Linux MCP over verified HTTPS."""

import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_jwt_fixture import ISSUER, create, token

ROOT = Path(__file__).resolve().parents[1]
SERVER = Path(sys.executable).with_name("linux-mcp-server")


class DiagnosticsHTTPS(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.url = f"https://localhost:{cls.port}/mcp"
        cls.signing_key = create(cls.directory)
        cls.token = token(cls.signing_key)
        cls.jwks_server = ThreadingHTTPServer(("127.0.0.1", 0), partial(
            SimpleHTTPRequestHandler, directory=str(cls.directory)))
        issuer_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        issuer_tls.load_cert_chain(cls.directory / "tls.crt", cls.directory / "tls.key")
        cls.jwks_server.socket = issuer_tls.wrap_socket(cls.jwks_server.socket, server_side=True)
        cls.jwks_thread = Thread(target=cls.jwks_server.serve_forever, daemon=True)
        cls.jwks_thread.start()
        cls.log_file = cls.directory / "nginx-error.log"
        cls.log_file.write_text("permission denied opening /web/index.html\n" * 40)
        (cls.directory / "policy.yaml").write_text(
            "rules:\n  - claims:\n      azp: demojam-ao-rca\n      jti: test-token\n"
            "    host: localhost\n    tools: [get_disk_usage, read_log_file]\n    action: local\n"
        )
        cls.audit_file = cls.directory / "audit.log"
        # ENRICHED audit records contain a group separator. The upstream tool
        # splits that into multiple entries per physical line; retain its behavior.
        cls.audit_file.write_text(('type=AVC msg=audit(1:1): avc: denied { read } '
                                   'comm="nginx" tcontext=default_t\x1dUID="root"\n') * 40)
        cls.verify = ssl.create_default_context(cafile=str(cls.directory / "tls.crt"))
        cls.process = None
        cls.start_server()

    @classmethod
    def start_server(cls):
        if cls.process is not None:
            cls.process.terminate()
            cls.process.wait(timeout=15)
        env = os.environ | {
            "LINUX_MCP_TRANSPORT": "http", "LINUX_MCP_HOST_MODE": "local-only",
            "LINUX_MCP_TOOLSET": "fixed", "LINUX_MCP_HOST": "127.0.0.1",
            "LINUX_MCP_PORT": str(cls.port),
            "LINUX_MCP_TLS_CERT": str(cls.directory / "tls.crt"),
            "LINUX_MCP_TLS_KEY": str(cls.directory / "tls.key"),
            "LINUX_MCP_POLICY_PATH": str(cls.directory / "policy.yaml"),
            "LINUX_MCP_ALLOWED_LOG_PATHS": f"{cls.log_file},{cls.audit_file}",
            "LINUX_MCP_LOG_DIR": str(cls.directory / "logs"),
            "LINUX_MCP_AUTH__PROVIDER": "jwt", "LINUX_MCP_AUTH__JWT__ISSUER": ISSUER,
            "LINUX_MCP_AUTH__JWT__AUDIENCE": "webapp-mcp",
            "LINUX_MCP_AUTH__JWT__JWKS_URI": f"https://localhost:{cls.jwks_server.server_port}/jwks.json",
            "SSL_CERT_FILE": str(cls.directory / "tls.crt"),
        }
        cls.process = subprocess.Popen(
            [str(SERVER)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError("MCP service exited before readiness")
            try:
                with httpx.Client(verify=cls.verify, trust_env=False) as client:
                    if client.get(cls.url).status_code == 401:
                        return
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        raise RuntimeError("MCP listener was not ready")

    @classmethod
    def tearDownClass(cls):
        if cls.process is not None:
            cls.process.terminate()
            cls.process.wait(timeout=15)
        cls.jwks_server.shutdown()
        cls.jwks_server.server_close()
        cls.jwks_thread.join()
        cls.temp.cleanup()

    async def session_checks(self, token):
        # This is the same SDK transport used by AO's shipped MCP adapter.
        async with httpx.AsyncClient(
            verify=self.verify, headers={"Authorization": f"Bearer {token}"}, trust_env=False,
        ) as client:
            async with streamable_http_client(self.url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.send_ping()
                    tools = await session.list_tools()
                    self.assertNotIn("run_script", {tool.name for tool in tools.tools})
                    self.assertNotIn("get_selinux_denials", {tool.name for tool in tools.tools})
                    audit_result = await session.call_tool("read_log_file", {
                        "log_path": str(self.audit_file), "last_lines": 20,
                    })
                    self.assertFalse(audit_result.isError)
                    self.assertIn("default_t", str(audit_result.content))
                    result = await session.call_tool("get_disk_usage", {})
                    self.assertFalse(result.isError)
                    allowed = await session.call_tool("read_log_file", {"log_path": str(self.log_file)})
                    self.assertFalse(allowed.isError)
                    self.assertIn("permission denied", str(allowed.content))
                    bounded = await session.call_tool("read_log_file", {
                        "log_path": str(self.log_file), "first_lines": 20,
                    })
                    self.assertFalse(bounded.isError)
                    self.assertEqual(len(bounded.structuredContent["entries"]), 20)
                    denied = await session.call_tool("read_file", {"path": "/etc/passwd"})
                    self.assertTrue(denied.isError)
                    denied_log = await session.call_tool("read_log_file", {"log_path": "/etc/passwd"})
                    self.assertTrue(denied_log.isError)
                    denied_host = await session.call_tool("get_disk_usage", {"host": "other.example.com"})
                    self.assertTrue(denied_host.isError)

    async def test_authenticated_diagnostics_and_denials(self):
        await self.session_checks(self.token)
        async with httpx.AsyncClient(verify=self.verify, trust_env=False) as client:
            self.assertEqual((await client.get(self.url)).status_code, 401)
            self.assertEqual((await client.get(self.url, headers={"Authorization": "Bearer wrong"})).status_code, 401)

    async def test_jwt_claims_and_signature(self):
        invalid_tokens = [
            token(self.signing_key, exp=int(time.time()) - 3600),
            token(self.signing_key, iss="https://wrong-issuer.test"),
            token(self.signing_key, aud="other-service"),
            token(create(self.directory / "other-issuer")),
        ]
        async with httpx.AsyncClient(verify=self.verify, trust_env=False) as client:
            for credential in invalid_tokens:
                with self.subTest(credential_type="invalid JWT"):
                    self.assertEqual((await client.get(self.url, headers={
                        "Authorization": f"Bearer {credential}",
                    })).status_code, 401)
        # Valid JWTs for another client or a rotated-out token must fail policy.
        for claims in ({"azp": "other-client"}, {"jti": "rotated-out-token"}):
            async with httpx.AsyncClient(verify=self.verify, trust_env=False, headers={
                "Authorization": f"Bearer {token(self.signing_key, **claims)}",
            }) as client:
                async with streamable_http_client(self.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool("get_disk_usage", {})
                        self.assertTrue(result.isError)

    @unittest.skipUnless(os.environ.get("WEBAPP_MCP_AO_IMAGE"), "Set WEBAPP_MCP_AO_IMAGE for the shipped AO adapter check")
    async def test_shipped_ao_adapter(self):
        fixture = self.directory / "ao-fixture.json"
        fixture.write_text(json.dumps({
            "token": self.token, "url": self.url, "ca_path": "/fixture/tls.crt",
        }))
        fixture.chmod(0o600)
        result = subprocess.run([
            "podman", "run", "--rm", "--network", "host", "--user", "0",
            "-e", 'APP_INTEGRATION_URL_ALLOWED_HOSTS=["localhost"]',
            "-v", f"{self.directory}:/fixture:ro,Z",
            "-v", f"{ROOT / 'tests/ao_mcp_probe.py'}:/probe.py:ro,Z",
            "--entrypoint", "python", os.environ["WEBAPP_MCP_AO_IMAGE"],
            "/probe.py", "/fixture/ao-fixture.json",
        ], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

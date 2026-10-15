"""Exercise the deployed adapter and real Linux MCP over verified HTTPS."""

import hashlib
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "playbooks/webapp/files/webapp-mcp-server.py"


class DiagnosticsHTTPS(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.url = f"https://localhost:{cls.port}/mcp"
        cls.token = secrets.token_hex(32)
        cls.log_file = cls.directory / "nginx-error.log"
        cls.log_file.write_text("permission denied opening /web/index.html\n" * 40)
        (cls.directory / "policy.yaml").write_text(
            "rules:\n  - claims:\n      client_id: demojam-ao-rca\n"
            "    host: localhost\n    tools: [get_disk_usage, read_log_file, get_selinux_denials]\n    action: local\n"
        )
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
             "-keyout", str(cls.directory / "tls.key"), "-out", str(cls.directory / "tls.crt")],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.verify = ssl.create_default_context(cafile=str(cls.directory / "tls.crt"))
        cls.process = None
        cls.start_server(cls.token)

    @classmethod
    def start_server(cls, token, expires_at=None):
        if cls.process is not None:
            cls.process.terminate()
            cls.process.wait(timeout=15)
        (cls.directory / "credential.json").write_text(json.dumps({
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": expires_at or int(time.time()) + 3600,
        }))
        (cls.directory / "audit-facts.json").write_text(json.dumps({
            "status": "observed", "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "events": [{"comm": "nginx", "target_context": "default_t"}], "limitations": [],
        }))
        env = os.environ | {
            "LINUX_MCP_TRANSPORT": "http", "LINUX_MCP_HOST_MODE": "local-only",
            "LINUX_MCP_TOOLSET": "fixed", "LINUX_MCP_HOST": "127.0.0.1",
            "LINUX_MCP_PORT": str(cls.port),
            "LINUX_MCP_TLS_CERT": str(cls.directory / "tls.crt"),
            "LINUX_MCP_TLS_KEY": str(cls.directory / "tls.key"),
            "LINUX_MCP_POLICY_PATH": str(cls.directory / "policy.yaml"),
            "LINUX_MCP_ALLOWED_LOG_PATHS": str(cls.log_file),
            "LINUX_MCP_LOG_DIR": str(cls.directory / "logs"),
            "WEBAPP_MCP_AUDIT_SNAPSHOT": str(cls.directory / "audit-facts.json"),
            "WEBAPP_MCP_CREDENTIAL_PATH": str(cls.directory / "credential.json"),
        }
        cls.process = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
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
                    audit_result = await session.call_tool("get_selinux_denials", {})
                    self.assertFalse(audit_result.isError)
                    self.assertEqual(audit_result.structuredContent["status"], "observed")
                    self.assertIn("default_t", str(audit_result.structuredContent))
                    result = await session.call_tool("get_disk_usage", {})
                    self.assertFalse(result.isError)
                    allowed = await session.call_tool("read_log_file", {"log_path": str(self.log_file)})
                    self.assertFalse(allowed.isError)
                    self.assertIn("permission denied", str(allowed.content))
                    bounded = await session.call_tool("read_log_file", {
                        "log_path": str(self.log_file), "first_lines": 20000,
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

    async def test_expiry_and_rotation(self):
        self.start_server(self.token, int(time.time()) - 1)
        async with httpx.AsyncClient(verify=self.verify, trust_env=False) as client:
            self.assertEqual((await client.get(self.url, headers={"Authorization": f"Bearer {self.token}"})).status_code, 401)
        replacement = secrets.token_hex(32)
        self.start_server(replacement)
        async with httpx.AsyncClient(verify=self.verify, trust_env=False) as client:
            self.assertEqual((await client.get(self.url, headers={"Authorization": f"Bearer {self.token}"})).status_code, 401)
        await self.session_checks(replacement)
        self.start_server(self.token)

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

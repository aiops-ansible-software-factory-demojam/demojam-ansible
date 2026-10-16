#!/usr/bin/env python3
"""Functional assertions for the real systemd guest service (test token only)."""

import asyncio
import time
from pathlib import Path

import jwt
import ssl

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    verify = ssl.create_default_context(cafile="/etc/webapp-mcp/ca.crt")
    token = jwt.encode({
        "iss": "https://issuer.test", "aud": "webapp-mcp", "sub": "test-service-account",
        "azp": "demojam-ao-rca", "jti": "test-token", "iat": int(time.time()), "exp": int(time.time()) + 3600,
    }, Path("/root/mcp-test-signing.key").read_text(), algorithm="RS256", headers={"kid": "test-key"})
    async with httpx.AsyncClient(
        verify=verify, trust_env=False,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        async with streamable_http_client("https://localhost:8443/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                # Append after reboot, just before reading: boot audit traffic
                # can push an earlier fixture out of the requested 20-line tail.
                # Keep auditd's inode intact while adding this test-only record.
                with Path("/var/log/audit/audit.log").open("a") as audit_log:
                    audit_log.write('type=AVC msg=audit(1:4242): avc: denied { read } '
                                    'comm="nginx" name="molecule-audit-fixture" tcontext=default_t\n')
                audit = await session.call_tool("read_log_file", {
                    "log_path": "/var/log/audit/audit.log", "last_lines": 20,
                })
                assert not audit.isError, str(audit.content)
                assert "molecule-audit-fixture" in str(audit.content)
                assert len(audit.structuredContent["entries"]) <= 20
                service = await session.call_tool("get_service_status", {"service_name": "nginx"})
                assert not service.isError and "running" in str(service.content)
                system = await session.call_tool("get_system_information", {})
                assert not system.isError, str(system.content)
                assert system.structuredContent["hostname"], "The guest hostname must be available"
                assert system.structuredContent["os_name"], "The guest OS must be available"
                cpu = await session.call_tool("get_cpu_information", {})
                assert not cpu.isError, str(cpu.content)
                assert cpu.structuredContent["logical_cores"] > 0
                memory = await session.call_tool("get_memory_information", {})
                assert not memory.isError, str(memory.content)
                assert memory.structuredContent["ram"]["total"] > 0
                network = await session.call_tool("get_network_connections", {})
                assert not network.isError, str(network.content)
                disk = await session.call_tool("get_disk_usage", {})
                assert not disk.isError
                log = await session.call_tool("read_log_file", {"log_path": "/var/log/nginx/access.log"})
                assert not log.isError, str(log.content)
                assert "GET / HTTP" in str(log.content), "The rotated log must contain the verified HTTP request"
                denied = await session.call_tool("read_file", {"path": "/etc/passwd"})
                assert denied.isError and "Authorization denied" in str(denied.content)
                denied = await session.call_tool("read_log_file", {"log_path": "/etc/shadow"})
                assert denied.isError
                denied = await session.call_tool("get_disk_usage", {"host": "other.example.com"})
                assert denied.isError
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "run_script" not in names and "get_selinux_denials" not in names
    print("Guest MCP: native audit, nginx, system, CPU, memory, network, disk and rotated logs readable; unauthorized access denied")


asyncio.run(main())

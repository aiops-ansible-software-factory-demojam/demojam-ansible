#!/usr/bin/env python3
"""Functional assertions for the real systemd guest service (test token only)."""

import asyncio
import json
import ssl

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    verify = ssl.create_default_context(cafile="/etc/webapp-mcp/ca.crt")
    async with httpx.AsyncClient(
        verify=verify, trust_env=False,
        headers={"Authorization": "Bearer molecule-diagnostics-token"},
    ) as client:
        async with streamable_http_client("https://localhost:8443/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for _ in range(15):
                    audit = await session.call_tool("get_selinux_denials", {})
                    if not audit.isError and audit.structuredContent.get("status") == "observed":
                        break
                    await asyncio.sleep(1)
                assert not audit.isError and audit.structuredContent["status"] == "observed"
                assert len(json.dumps(audit.structuredContent, ensure_ascii=False, separators=(",", ":"))) <= 3000
                assert any(event.get("name") == "molecule-audit-fixture" for event in audit.structuredContent["events"])
                service = await session.call_tool("get_service_status", {"service_name": "nginx"})
                assert not service.isError and "running" in str(service.content)
                disk = await session.call_tool("get_disk_usage", {})
                assert not disk.isError
                log = await session.call_tool("read_log_file", {"log_path": "/var/log/nginx/access.log"})
                assert not log.isError, str(log.content)
                assert "GET / HTTP" in str(log.content), "The rotated log must contain the verified HTTP request"
                denied = await session.call_tool("read_file", {"path": "/etc/passwd"})
                assert denied.isError and "Authorization denied" in str(denied.content)
                denied = await session.call_tool("read_log_file", {"log_path": "/var/log/audit/audit.log"})
                assert denied.isError
                assert "run_script" not in {tool.name for tool in (await session.list_tools()).tools}
    print("Guest MCP: bounded AVC facts, nginx, disk and approved logs readable; raw audit files and scripts denied")


asyncio.run(main())

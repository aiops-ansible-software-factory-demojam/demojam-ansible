#!/usr/bin/env python3
"""Functional assertions for the real systemd guest service (test token only)."""

import asyncio
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
    print("Guest MCP: nginx, disk and approved logs readable; general files, audit files and scripts denied")


asyncio.run(main())

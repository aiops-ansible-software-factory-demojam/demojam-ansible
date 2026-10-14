"""Run inside the pinned AO backend image against a private test fixture."""

import asyncio
import json
import sys
from pathlib import Path

from syntara.integrations.adapters.mcp_server import MCPServerAdapter
from syntara.integrations.models.integration_configuration import MCPServerConfigurationInput
from syntara.tool_manager.lib.providers.mcp.mcp_provider import MCPProvider


async def main():
    fixture = json.loads(Path(sys.argv[1]).read_text())
    ca = Path(fixture["ca_path"]).read_text()
    config = MCPServerConfigurationInput(base_url=fixture["url"], ca_certificate=ca)
    adapter = MCPServerAdapter(config)
    credentials = {"bearer_token": fixture["token"]}
    health = await adapter.validate(credentials, 15)
    assert health.success, health.error
    discovery = await adapter.discover(credentials, 15)
    assert discovery.success, discovery.error
    provider = MCPProvider(base_url=fixture["url"], api_key=fixture["token"], ca_certificate=ca)
    try:
        tools = await provider.get_base_tools()
        disk = next(tool for tool in tools if tool.name == "get_disk_usage")
        result = await disk.ainvoke({})
        assert result, "No disk diagnostic returned"
    finally:
        await provider.close()
    denied = await adapter.validate({"bearer_token": "invalid"}, 15)
    assert not denied.success
    print("AO adapter: TLS authentication, discovery, tool execution and invalid-token denial passed")


asyncio.run(main())

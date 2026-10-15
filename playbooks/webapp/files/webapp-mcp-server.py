#!/usr/bin/env python3
"""Run Linux MCP with the bearer credential supported by AO 2026.8."""

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.middleware import Middleware
from fastmcp.exceptions import ToolError


class DiagnosticsTokenVerifier(TokenVerifier):
    """Verify an expiring machine credential without storing its plaintext."""

    def __init__(self, credential_path):
        super().__init__(required_scopes=["webapp:diagnostics"])
        self.credential = json.loads(Path(credential_path).read_text())
        if not re.fullmatch(r"[0-9a-f]{64}", self.credential["token_sha256"]):
            raise ValueError("Invalid diagnostics credential digest")
        if not isinstance(self.credential["expires_at"], int):
            raise ValueError("Invalid diagnostics credential expiry")

    async def verify_token(self, token):
        if self.credential["expires_at"] <= time.time():
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(digest, self.credential["token_sha256"]):
            return None
        return AccessToken(
            token=token,
            client_id="demojam-ao-rca",
            scopes=["webapp:diagnostics"],
            expires_at=self.credential["expires_at"],
            claims={
                "sub": "demojam-ao-rca", "client_id": "demojam-ao-rca",
                "email": "ao-rca@demojam.invalid",
            },
        )


class DiagnosticsLimits(Middleware):
    """Keep service/log investigation confined to nginx and bounded reads."""

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        arguments = context.message.arguments = context.message.arguments or {}
        if name in {"get_service_status", "get_service_logs"}:
            if arguments.get("service_name") not in {"nginx", "nginx.service"}:
                raise ToolError("Only nginx service diagnostics are allowed")
        if name == "get_journal_logs":
            if arguments.get("unit", "nginx.service") not in {"nginx", "nginx.service"}:
                raise ToolError("Only the nginx journal is allowed")
            arguments["unit"] = "nginx.service"
        if name in {"get_journal_logs", "read_log_file", "get_service_logs"}:
            field = "lines" if name == "get_service_logs" else (
                "first_lines" if arguments.get("first_lines") is not None else "last_lines"
            )
            count = arguments.get(field)
            if count is None:
                count = 20
            if type(count) is not int or count < 1:
                raise ToolError("Log line count must be a positive integer")
            arguments[field] = min(count, 20)
        return await call_next(context)


def main():
    # Set authentication before FastMCP constructs the HTTP application. The
    # upstream authorization middleware still checks every tool against policy.
    from linux_mcp_server.config import CONFIG
    from linux_mcp_server.server import mcp
    from linux_mcp_server.target_host import target_host_from
    from webapp_mcp_audit import SNAPSHOT, read_snapshot

    @mcp.tool(tags={"fixed"}, annotations={"readOnlyHint": True, "destructiveHint": False})
    @target_host_from(lambda arguments: "localhost")
    def get_selinux_denials() -> dict:
        """Get fresh, bounded nginx AVC facts and SELinux mode from the local audit collector.

        Returns up to four events from the last 15 minutes, at most 3000 characters.
        An unavailable status requires the AAP audit fallback. No events does not
        rule out SELinux. Arbitrary paths, commands and raw audit reads are forbidden.
        """
        return read_snapshot(Path(os.environ.get("WEBAPP_MCP_AUDIT_SNAPSHOT", SNAPSHOT)))

    if CONFIG.transport.value != "http" or CONFIG.host_mode.value != "local-only":
        raise ValueError("Diagnostics requires HTTP transport and local-only execution")
    if CONFIG.toolset.value != "fixed" or CONFIG.policy_path is None:
        raise ValueError("Diagnostics requires fixed tools and an authorization policy")
    if CONFIG.tls_cert is None or CONFIG.tls_key is None:
        raise ValueError("Diagnostics requires a TLS certificate and key")
    mcp.auth = DiagnosticsTokenVerifier(os.environ["WEBAPP_MCP_CREDENTIAL_PATH"])
    mcp.add_middleware(DiagnosticsLimits())
    mcp.run(show_banner=False, transport="http", **CONFIG.transport_kwargs)


if __name__ == "__main__":
    main()

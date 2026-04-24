from __future__ import annotations

from fastmcp import FastMCP

from backend.bootstrap import create_services
from backend.mcp_app import create_dev_mcp_server
from backend.settings import get_settings


def server() -> FastMCP:
    settings = get_settings()
    services = create_services(settings)
    return create_dev_mcp_server(settings, services)

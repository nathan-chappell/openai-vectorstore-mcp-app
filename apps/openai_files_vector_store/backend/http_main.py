from __future__ import annotations

import logging
from pathlib import Path

from .logging import configure_logging
from .server import create_server
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    server = create_server(settings)
    logger = logging.getLogger(__name__)
    logger.info(
        "mcp_server_starting name=%s transport=streamable-http cwd=%s host=%s port=%s path=%s",
        settings.app_name,
        Path.cwd(),
        server.settings.host,
        server.settings.port,
        server.settings.streamable_http_path,
    )
    try:
        server.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info(
            "mcp_server_stopped name=%s transport=streamable-http",
            settings.app_name,
        )


if __name__ == "__main__":
    main()

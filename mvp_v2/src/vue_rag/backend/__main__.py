from __future__ import annotations

import uvicorn

from vue_rag.backend.config import GatewaySettings
from vue_rag.runtime_logging import configure_runtime_logging


def main() -> None:
    settings = GatewaySettings.from_environment()
    configure_runtime_logging(
        settings.log_level,
        component="vue_rag.backend",
        secrets=(settings.backend_token, settings.selora_api_key),
    )
    uvicorn.run(
        "vue_rag.backend.app:create_application",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        access_log=settings.log_level == "DEBUG",
        log_config=None,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

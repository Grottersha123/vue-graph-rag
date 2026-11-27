from __future__ import annotations

import uvicorn

from vue_rag.backend.config import GatewaySettings


def main() -> None:
    settings = GatewaySettings.from_environment()
    uvicorn.run(
        "vue_rag.backend.app:create_application",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        access_log=True,
    )


if __name__ == "__main__":
    main()

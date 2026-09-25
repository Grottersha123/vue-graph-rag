from __future__ import annotations

import uvicorn

from mvp_v2.backend.config import GatewaySettings


def main() -> None:
    settings = GatewaySettings.from_environment()
    uvicorn.run(
        "mvp_v2.backend.app:create_application",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        access_log=True,
    )


if __name__ == "__main__":
    main()

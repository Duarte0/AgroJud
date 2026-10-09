"""Initial worker command: validate configuration and exit without polling."""

import json

from agrojud.config import get_settings


def main() -> None:
    settings = get_settings()
    print(
        json.dumps(
            {
                "event": "worker_configuration_validated",
                "environment": settings.environment,
                "processing_enabled": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

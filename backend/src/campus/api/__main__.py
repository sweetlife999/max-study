"""Run with python -m campus.api."""

import uvicorn

from campus.api.app import create_app
from campus.config import Settings
from campus.logs import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings=settings),
        host="0.0.0.0",  # noqa: S104 - container listens on its published interface
        port=8000,
        log_config=None,
        access_log=False,
        date_header=False,
    )


if __name__ == "__main__":
    main()

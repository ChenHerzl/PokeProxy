"""Application events deliberately exclude exception text and arbitrary request data."""

import json
import logging
import sys
from datetime import UTC, datetime

logger = logging.getLogger("pokeproxy")


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                **getattr(record, "context", {}),
            }
        )


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def event(name: str, **context: object) -> None:
    logger.info(name, extra={"context": context})

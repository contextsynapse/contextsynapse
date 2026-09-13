"""
Structured Logging Configuration
=================================
Configures structlog for JSON output in production and colored console in dev.

Usage::

    from contextsynapse.logging_config import setup_logging
    setup_logging()  # call once at startup

    import structlog
    logger = structlog.get_logger()
    logger.info("query_executed", query="SELECT * FROM Person", duration_ms=12.3)
"""

import logging
import os
import sys

_CONFIGURED = False


def setup_logging():
    """Configure structlog + stdlib logging integration."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    try:
        import structlog
    except ImportError:
        # structlog not installed — fall back to basic stdlib logging
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        )
        return

    is_prod = os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "development").lower() in ("production", "prod")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    # Shared processors for both dev and prod
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_prod:
        # Production: JSON output to stdout
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: colored console output
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Quiet noisy loggers
    for name in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

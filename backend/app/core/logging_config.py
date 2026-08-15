"""Logging configuration for the application.

Outputs JSON lines when LOG_FORMAT=json (useful in Docker for aggregators),
otherwise plain human-readable text.
"""

import logging
import os
import sys


def setup_logging() -> None:
    # We don't strictly need a JSON logging library, we can just format it ourselves
    # since we control the log messages. For a true JSON structlog setup we'd need
    # to add a dependency, which is out of scope. We'll use a basic formatter.
    
    log_format = os.environ.get("LOG_FORMAT", "text").lower()
    
    if log_format == "json":
        # A simple JSON formatter using the standard library
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                import json
                import datetime
                
                log_record = {
                    "timestamp": datetime.datetime.utcfromtimestamp(record.created).isoformat() + "Z",
                    "level": record.levelname,
                    "name": record.name,
                    "message": record.getMessage(),
                }
                
                if record.exc_info:
                    log_record["exception"] = self.formatException(record.exc_info)
                    
                return json.dumps(log_record)
                
        formatter = JsonFormatter()
    else:
        # Standard text format
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Quiet down some noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

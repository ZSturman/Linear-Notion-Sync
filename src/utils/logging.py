"""Structured logging for Notion-Linear sync service."""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Optional


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging in production."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, "entity_type"):
            log_data["entity_type"] = record.entity_type
        if hasattr(record, "notion_id"):
            log_data["notion_id"] = record.notion_id
        if hasattr(record, "linear_id"):
            log_data["linear_id"] = record.linear_id
        if hasattr(record, "action"):
            log_data["action"] = record.action
        if hasattr(record, "dry_run"):
            log_data["dry_run"] = record.dry_run
            
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)


class HumanFormatter(logging.Formatter):
    """Human-readable formatter for development."""
    
    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[35m", # Magenta
        "RESET": "\033[0m",
    }
    
    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]
        
        # Build context string from extra fields
        context_parts = []
        if hasattr(record, "entity_type"):
            context_parts.append(f"type={record.entity_type}")
        if hasattr(record, "action"):
            context_parts.append(f"action={record.action}")
        if hasattr(record, "notion_id"):
            context_parts.append(f"notion={record.notion_id[:8]}...")
        if hasattr(record, "linear_id"):
            context_parts.append(f"linear={record.linear_id[:8]}...")
        if hasattr(record, "dry_run") and record.dry_run:
            context_parts.append("DRY-RUN")
            
        context = f" [{', '.join(context_parts)}]" if context_parts else ""
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"{timestamp} {color}{record.levelname:8}{reset} {record.name}: {record.getMessage()}{context}"
        
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
            
        return formatted


class SyncLogger(logging.LoggerAdapter):
    """Logger adapter that adds sync context to log messages."""
    
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        # Merge extra context
        extra = kwargs.get("extra", {})
        extra.update(self.extra)
        kwargs["extra"] = extra
        return msg, kwargs
    
    def with_context(
        self,
        entity_type: Optional[str] = None,
        notion_id: Optional[str] = None,
        linear_id: Optional[str] = None,
        action: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> "SyncLogger":
        """Create a new logger with additional context."""
        new_extra = dict(self.extra)
        if entity_type is not None:
            new_extra["entity_type"] = entity_type
        if notion_id is not None:
            new_extra["notion_id"] = notion_id
        if linear_id is not None:
            new_extra["linear_id"] = linear_id
        if action is not None:
            new_extra["action"] = action
        if dry_run is not None:
            new_extra["dry_run"] = dry_run
        return SyncLogger(self.logger, new_extra)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
) -> None:
    """Configure logging for the sync service.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON formatting (for production)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Set formatter
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(HumanFormatter())
    
    root_logger.addHandler(handler)


def get_logger(name: str, **extra: Any) -> SyncLogger:
    """Get a logger instance with optional extra context.
    
    Args:
        name: Logger name (usually __name__)
        **extra: Extra context fields (entity_type, notion_id, etc.)
        
    Returns:
        SyncLogger instance
    """
    logger = logging.getLogger(name)
    return SyncLogger(logger, extra)

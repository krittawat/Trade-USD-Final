from __future__ import annotations


class StructuredEventLogger:
    def __init__(self, logger):
        self.logger = logger

    def log(self, level: str, event: str, message: str | None = None, **payload) -> None:
        extra = {"metrics": {"event": event, **payload}}
        getattr(self.logger, level)(message or event, extra=extra)

    def info(self, event: str, message: str | None = None, **payload) -> None:
        self.log("info", event, message, **payload)

    def warning(self, event: str, message: str | None = None, **payload) -> None:
        self.log("warning", event, message, **payload)

    def error(self, event: str, message: str | None = None, **payload) -> None:
        self.log("error", event, message, **payload)

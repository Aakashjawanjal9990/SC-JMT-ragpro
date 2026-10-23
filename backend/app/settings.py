"""Small environment-backed settings object; no secret values are logged."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> "Settings":
        environment = os.getenv("APP_ENV", "development").strip() or "development"
        if len(environment) > 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", environment):
            environment = "development"
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            log_level = "INFO"
        return cls(environment=environment, log_level=log_level)

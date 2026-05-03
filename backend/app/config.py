"""Конфигурация приложения из переменных окружения."""

from __future__ import annotations

import os
import sys

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("FATAL: переменная окружения DATABASE_URL не задана")

_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]
if not ALLOWED_ORIGINS:
    sys.exit(
        "FATAL: переменная окружения ALLOWED_ORIGINS не задана "
        "(пример: ALLOWED_ORIGINS=http://localhost:5173)"
    )

RATE_LIMIT_CALLS: int = int(os.getenv("RATE_LIMIT_CALLS", "60"))
RATE_LIMIT_PERIOD: int = int(os.getenv("RATE_LIMIT_PERIOD", "60"))
if RATE_LIMIT_CALLS <= 0 or RATE_LIMIT_PERIOD <= 0:
    sys.exit("FATAL: RATE_LIMIT_CALLS и RATE_LIMIT_PERIOD должны быть > 0")

"""Конфигурация приложения из переменных окружения."""

from __future__ import annotations

import os

DATABASE_URL: str = os.environ["DATABASE_URL"]

ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if o.strip()
]

RATE_LIMIT_CALLS: int = int(os.getenv("RATE_LIMIT_CALLS", "60"))
RATE_LIMIT_PERIOD: int = int(os.getenv("RATE_LIMIT_PERIOD", "60"))

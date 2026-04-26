"""Конфигурация приложения из переменных окружения."""

from __future__ import annotations

import os

DATABASE_URL: str = os.environ["DATABASE_URL"]

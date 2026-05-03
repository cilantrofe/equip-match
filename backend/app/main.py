"""Точка входа FastAPI-приложения."""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import router as api_router
from app.config import ALLOWED_ORIGINS, RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
)

_log = logging.getLogger(__name__)


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware для ограничения частоты запросов по IP-адресу клиента."""

    def __init__(self, app: FastAPI, calls: int, period: int) -> None:
        """Инициализировать с лимитом `calls` запросов за `period` секунд."""
        super().__init__(app)
        self._calls = calls
        self._period = period
        self._buckets: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        """Пропустить запрос или вернуть 429, если лимит для IP исчерпан."""
        xff = request.headers.get("x-forwarded-for")
        client = xff.split(",")[0].strip() if xff else (
            request.client.host if request.client else "unknown"
        )
        now = time.monotonic()
        cutoff = now - self._period

        bucket = self._buckets.get(client)
        if bucket is not None:
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                del self._buckets[client]
                bucket = None

        if bucket is None:
            bucket = deque()
            self._buckets[client] = bucket

        if len(bucket) >= self._calls:
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={"Retry-After": str(self._period)},
            )
        bucket.append(now)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: запустить планировщик при старте, остановить при завершении."""
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Product Matcher", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("Unhandled error on %s %s — %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(_RateLimitMiddleware, calls=RATE_LIMIT_CALLS, period=RATE_LIMIT_PERIOD)  # type: ignore[arg-type]

app.include_router(api_router, prefix="/api")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def root():
    """Отдать главную страницу SPA."""
    return FileResponse("static/index.html")

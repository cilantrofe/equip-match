"""Точка входа FastAPI-приложения."""

from __future__ import annotations

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


class _RateLimitMiddleware(BaseHTTPMiddleware):

    def __init__(self, app: FastAPI, calls: int, period: int) -> None:
        super().__init__(app)
        self._calls = calls
        self._period = period
        self._buckets: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = self._buckets.setdefault(client, deque())
        cutoff = now - self._period
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
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
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Product Matcher", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(_RateLimitMiddleware, calls=RATE_LIMIT_CALLS, period=RATE_LIMIT_PERIOD)

app.include_router(api_router, prefix="/api")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")

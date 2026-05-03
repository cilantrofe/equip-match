import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://fake:fake@localhost/fake")

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.router import router as api_router


def make_spec(
    canonical,
    num=None,
    text=None,
    weight=None,
    name=None,
):
    return SimpleNamespace(
        spec_name=name or canonical,
        spec_name_canonical=canonical,
        spec_value_num=num,
        spec_value_text=text,
        weight=weight,
    )


def make_product(
    brand="BrandA", price=1000.0, specs=None, sku="SKU001", category="cam"
):
    return SimpleNamespace(
        id=1,
        source_sku=sku,
        brand=brand,
        model="M1",
        category=category,
        price=price,
        url=None,
        specs=specs or [],
    )


@pytest.fixture
def _make_spec():
    return make_spec


@pytest.fixture
def _make_product():
    return make_product


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    return app


@pytest.fixture
async def app_client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client

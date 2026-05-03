"""Интеграционные тесты router.py. Все вызовы базы данных и служб имитируются."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

MOCK_PRODUCT = {
    "id": 1,
    "sku": "SKU-001",
    "category": "cam",
    "brand": "Hikvision",
    "model": "DS-2CD",
    "price": 5000.0,
    "url": None,
}

MOCK_CANDIDATE = {
    **{k: v for k, v in MOCK_PRODUCT.items()},
    "id": 2,
    "sku": "SKU-002",
    "brand": "Dahua",
    "price": 4800.0,
    "score": 0.85,
    "breakdown": [],
}

MOCK_LOOKUP_RESPONSE = {
    "query": MOCK_PRODUCT,
    "candidates": [MOCK_CANDIDATE],
}


def _mock_async_session(session_mock=None):
    """Return a mock that acts like async_session() context manager."""
    if session_mock is None:
        session_mock = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    callable_mock = MagicMock(return_value=cm)
    return callable_mock


# GET /api/brands


async def test_get_brands_success(app_client, mocker):
    mocker.patch("app.api.router.async_session", _mock_async_session())
    mocker.patch(
        "app.api.router.get_brands", AsyncMock(return_value=["Dahua", "Hikvision"])
    )

    r = await app_client.get("/api/brands")
    assert r.status_code == 200
    assert r.json() == {"brands": ["Dahua", "Hikvision"]}


async def test_get_brands_empty(app_client, mocker):
    mocker.patch("app.api.router.async_session", _mock_async_session())
    mocker.patch("app.api.router.get_brands", AsyncMock(return_value=[]))

    r = await app_client.get("/api/brands")
    assert r.status_code == 200
    assert r.json() == {"brands": []}


# GET /api/status


async def test_get_status_with_datetime(app_client, mocker):
    dt = datetime(2024, 1, 15, 10, 30, 0)
    mocker.patch("app.api.router.async_session", _mock_async_session())
    mocker.patch("app.api.router.get_last_updated", AsyncMock(return_value=dt))

    r = await app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "last_updated" in body
    assert body["last_updated"] is not None


async def test_get_status_none(app_client, mocker):
    mocker.patch("app.api.router.async_session", _mock_async_session())
    mocker.patch("app.api.router.get_last_updated", AsyncMock(return_value=None))

    r = await app_client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["last_updated"] is None


# GET /api/lookup/price


async def test_lookup_price_success(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_price", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )

    r = await app_client.get("/api/lookup/price?sku=SKU-001")
    assert r.status_code == 200
    body = r.json()
    assert body["query"]["sku"] == "SKU-001"
    assert len(body["candidates"]) == 1


async def test_lookup_price_not_found(app_client, mocker):
    mocker.patch("app.api.router.lookup_price", AsyncMock(return_value=None))

    r = await app_client.get("/api/lookup/price?sku=UNKNOWN")
    assert r.status_code == 404
    assert r.json()["detail"] == "Product not found"


async def test_lookup_price_missing_sku(app_client):
    r = await app_client.get("/api/lookup/price")
    assert r.status_code == 422


async def test_lookup_price_limit_too_small(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_price", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )
    r = await app_client.get("/api/lookup/price?sku=SKU-001&limit=0")
    assert r.status_code == 422


async def test_lookup_price_limit_too_large(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_price", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )
    r = await app_client.get("/api/lookup/price?sku=SKU-001&limit=99")
    assert r.status_code == 422


async def test_lookup_price_limit_boundary_valid(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_price", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )
    r1 = await app_client.get("/api/lookup/price?sku=SKU-001&limit=1")
    r20 = await app_client.get("/api/lookup/price?sku=SKU-001&limit=20")
    assert r1.status_code == 200
    assert r20.status_code == 200


async def test_lookup_price_limit_passed_to_service(app_client, mocker):
    service = AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    mocker.patch("app.api.router.lookup_price", service)

    await app_client.get("/api/lookup/price?sku=SKU-001&limit=3")
    service.assert_awaited_once()
    _, kwargs = service.call_args
    assert kwargs["limit"] == 3


async def test_lookup_price_brand_passed_to_service(app_client, mocker):
    service = AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    mocker.patch("app.api.router.lookup_price", service)

    await app_client.get("/api/lookup/price?sku=SKU-001&brand=Hikvision")
    _, kwargs = service.call_args
    assert kwargs["brand"] == "Hikvision"


# POST /api/lookup/tech


async def test_lookup_tech_success(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_tech", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )

    r = await app_client.post("/api/lookup/tech", json={"sku": "SKU-001"})
    assert r.status_code == 200
    body = r.json()
    assert "query" in body
    assert "candidates" in body


async def test_lookup_tech_not_found(app_client, mocker):
    mocker.patch("app.api.router.lookup_tech", AsyncMock(return_value=None))

    r = await app_client.post("/api/lookup/tech", json={"sku": "UNKNOWN"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Product not found"


async def test_lookup_tech_missing_sku(app_client):
    r = await app_client.post("/api/lookup/tech", json={"limit": 5})
    assert r.status_code == 422


async def test_lookup_tech_empty_sku(app_client):
    r = await app_client.post("/api/lookup/tech", json={"sku": ""})
    assert r.status_code == 422


async def test_lookup_tech_too_many_weight_overrides(app_client):
    weights = {f"voltage": 1.0}
    known = [
        "voltage",
        "power",
        "ip_rating",
        "temperature_range",
        "display_resolution",
        "weight",
        "brightness",
    ]
    weights = {k: 1.0 for k in known[:7]}
    r = await app_client.post(
        "/api/lookup/tech", json={"sku": "SKU-001", "weights": weights}
    )
    assert r.status_code == 422


async def test_lookup_tech_unknown_canonical_in_weights(app_client):
    r = await app_client.post(
        "/api/lookup/tech",
        json={"sku": "SKU-001", "weights": {"totally_fake_canonical_xyz_abc": 2.0}},
    )
    assert r.status_code == 422
    body = r.json()
    assert "неизвестные канонические имена" in str(body)


async def test_lookup_tech_weight_zero_invalid(app_client):
    r = await app_client.post(
        "/api/lookup/tech",
        json={"sku": "SKU-001", "weights": {"voltage": 0.0}},
    )
    assert r.status_code == 422


async def test_lookup_tech_weight_too_high(app_client):
    r = await app_client.post(
        "/api/lookup/tech",
        json={"sku": "SKU-001", "weights": {"voltage": 11.0}},
    )
    assert r.status_code == 422


async def test_lookup_tech_weight_boundary_valid(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_tech", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )

    r = await app_client.post(
        "/api/lookup/tech",
        json={"sku": "SKU-001", "weights": {"voltage": 10.0}},
    )
    assert r.status_code == 200


async def test_lookup_tech_limit_boundaries(app_client, mocker):
    mocker.patch(
        "app.api.router.lookup_tech", AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    )

    r1 = await app_client.post("/api/lookup/tech", json={"sku": "SKU-001", "limit": 1})
    r20 = await app_client.post(
        "/api/lookup/tech", json={"sku": "SKU-001", "limit": 20}
    )
    r21 = await app_client.post(
        "/api/lookup/tech", json={"sku": "SKU-001", "limit": 21}
    )
    assert r1.status_code == 200
    assert r20.status_code == 200
    assert r21.status_code == 422


async def test_lookup_tech_brand_passed_to_service(app_client, mocker):
    service = AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    mocker.patch("app.api.router.lookup_tech", service)

    await app_client.post(
        "/api/lookup/tech", json={"sku": "SKU-001", "brand": "Hikvision"}
    )
    _, kwargs = service.call_args
    assert kwargs["brand"] == "Hikvision"


async def test_lookup_tech_weights_passed_to_service(app_client, mocker):
    service = AsyncMock(return_value=MOCK_LOOKUP_RESPONSE)
    mocker.patch("app.api.router.lookup_tech", service)

    await app_client.post(
        "/api/lookup/tech",
        json={"sku": "SKU-001", "weights": {"voltage": 5.0}},
    )
    _, kwargs = service.call_args
    assert kwargs["weight_overrides"] == {"voltage": 5.0}

"""HTTP-роуты API.

- `GET  /lookup/price` — top-N ближайших по цене, только `sku` и `limit`.
- `POST /lookup/tech`  — top-N по характеристикам, принимает JSON-тело
  с `sku`, `limit` и опциональным `weights` — мапой `{canonical: float}`.
  Веса из запроса перекрывают дефолты и БД и позволяют менеджеру
  крутить акценты под конкретную заявку.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.db.crud import get_brands, get_last_updated
from app.db.session import async_session
from app.services.lookup import DEFAULT_LIMIT, lookup_price, lookup_tech
from app.normalization.spec_aliases import SPEC_ALIASES, WEIGHT_DEFAULTS


router = APIRouter()


MAX_WEIGHT_OVERRIDES = 6
MAX_WEIGHT_VALUE = 10.0

KNOWN_CANONICALS: frozenset[str] = frozenset(SPEC_ALIASES.values()) | frozenset(
    WEIGHT_DEFAULTS.keys()
)


class ProductView(BaseModel):
    """Краткая карточка товара в ответе API."""

    id: Optional[int] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    price: Optional[float] = None
    url: Optional[str] = None


class FeatureView(BaseModel):
    """Вклад одной характеристики в скор кандидата."""

    name: str
    target: Optional[str] = None
    candidate: Optional[str] = None
    similarity: float
    weight: float
    contribution: float
    note: Optional[str] = None


class CandidateView(ProductView):
    """Кандидат с итоговым скором и разбивкой по характеристикам."""

    score: float
    breakdown: list[FeatureView] = []


class LookupResponse(BaseModel):
    """Ответ эндпойнтов `/lookup/*`: целевой товар и список кандидатов."""

    query: ProductView
    candidates: list[CandidateView]


class TechLookupRequest(BaseModel):
    """Тело запроса `POST /lookup/tech`."""

    sku: str = Field(..., min_length=1)
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=20)
    brand: Optional[str] = None
    weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > MAX_WEIGHT_OVERRIDES:
            raise ValueError(f"не более {MAX_WEIGHT_OVERRIDES} оверрайдов весов за раз")
        unknown = [k for k in value if k not in KNOWN_CANONICALS]
        if unknown:
            raise ValueError(
                "неизвестные канонические имена: " + ", ".join(sorted(unknown))
            )
        bad = [k for k, w in value.items() if not (0.0 < float(w) <= MAX_WEIGHT_VALUE)]
        if bad:
            raise ValueError(
                f"веса должны быть в диапазоне (0, {MAX_WEIGHT_VALUE}]: "
                + ", ".join(sorted(bad))
            )
        return value


@router.get("/health")
async def health_endpoint() -> dict:
    """Проверить доступность приложения и базы данных."""
    async with async_session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/brands")
async def brands_endpoint() -> dict:
    """Вернуть отсортированный список всех брендов из базы данных."""
    async with async_session() as session:
        brands = await get_brands(session)
    return {"brands": brands}


@router.get("/status")
async def status_endpoint() -> dict:
    """Вернуть время последнего обновления товаров (`null`, если данных ещё нет)."""
    async with async_session() as session:
        last_updated = await get_last_updated(session)
    return {"last_updated": last_updated}


@router.get("/lookup/price", response_model=LookupResponse)
async def lookup_price_endpoint(
    sku: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=20),
    brand: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Найти top-N товаров, ближайших по цене к товару с указанным `sku`."""
    result = await lookup_price(sku, limit=limit, brand=brand or None)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return result


@router.post("/lookup/tech", response_model=LookupResponse)
async def lookup_tech_endpoint(
    payload: TechLookupRequest,
) -> dict[str, Any]:
    """Найти top-N товаров, похожих по характеристикам на товар с указанным `sku`."""
    result = await lookup_tech(
        payload.sku,
        limit=payload.limit,
        weight_overrides=payload.weights or None,
        brand=payload.brand or None,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return result

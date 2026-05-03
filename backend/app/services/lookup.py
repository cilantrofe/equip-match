"""Сервис поиска похожих товаров по SKU.

Два независимых сценария: по характеристикам (`lookup_tech`) и по цене
(`lookup_price`). Оба достают target из БД, поднимают кандидатов той
же категории (с отсечением самого target в SQL) и прогоняют через
соответствующий матчер.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db.crud import get_product_by_sku, get_products_in_category
from app.db.session import async_session
from app.matching.matcher import (
    FeatureContribution,
    MatchResult,
    WeightOverrides,
    match_by_price,
    match_by_tech,
)


DEFAULT_LIMIT = 5


async def lookup_tech(
    sku: str,
    limit: int = DEFAULT_LIMIT,
    weight_overrides: Optional[WeightOverrides] = None,
    brand: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Вернуть target и top-N похожих по характеристикам.

    `weight_overrides` — словарь `{canonical_name: weight}` из запроса,
    перекрывает дефолты и БД-значения. Менеджер прокидывает сюда акценты
    конкретной заявки, не трогая глобальные настройки.
    """
    async with async_session() as session:
        target = await get_product_by_sku(session, sku, brand=brand)
        if not target:
            return None
        candidates = await get_products_in_category(
            session,
            str(target.category or ""),
            exclude_product_id=int(target.id),  # type: ignore[arg-type]
        )
        results = match_by_tech(
            target,
            list(candidates),
            limit=limit,
            weight_overrides=weight_overrides,
        )
        return {
            "query": _product_view(target),
            "candidates": [_match_view(r) for r in results],
        }


async def lookup_price(
    sku: str,
    limit: int = DEFAULT_LIMIT,
    brand: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Вернуть target и top-N ближайших по цене."""
    async with async_session() as session:
        target = await get_product_by_sku(session, sku, brand=brand)
        if not target:
            return None
        candidates = await get_products_in_category(
            session,
            str(target.category or ""),
            exclude_product_id=int(target.id),  # type: ignore[arg-type]
        )
        results = match_by_price(target, list(candidates), limit=limit)
        return {
            "query": _product_view(target),
            "candidates": [_match_view(r) for r in results],
        }


def _product_view(product: object) -> dict[str, Any]:
    """Краткая карточка товара для ответа API."""
    price = getattr(product, "price", None)
    return {
        "id": getattr(product, "id", None),
        "sku": getattr(product, "source_sku", None),
        "category": getattr(product, "category", None),
        "brand": getattr(product, "brand", None),
        "model": getattr(product, "model", None),
        "price": float(price) if price is not None else None,
        "url": getattr(product, "url", None),
    }


def _match_view(result: MatchResult) -> dict[str, Any]:
    """Карточка кандидата с скором и разбивкой вклада характеристик."""
    view = _product_view(result.candidate)
    view["score"] = result.score
    view["breakdown"] = [_feature_view(f) for f in result.breakdown]
    return view


def _feature_view(feature: FeatureContribution) -> dict[str, Any]:
    """Сериализовать `FeatureContribution` в словарь для ответа API."""
    return {
        "name": feature.name,
        "target": feature.target,
        "candidate": feature.candidate,
        "similarity": feature.similarity,
        "weight": float(feature.weight),
        "contribution": round(feature.contribution, 3),
        "note": feature.note,
    }

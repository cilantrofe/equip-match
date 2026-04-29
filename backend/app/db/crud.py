"""CRUD-операции над товарами, характеристиками и источниками."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Product, ProductSpec, Source

_log = logging.getLogger(__name__)

_MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "500"))


async def get_product_by_sku(
    session: AsyncSession,
    sku: str,
    brand: Optional[str] = None,
) -> Optional[Product]:
    """Найти товар по `source_sku` с предзагрузкой характеристик.

    Если передан `brand` — дополнительно фильтрует по нему.
    """
    q = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(Product.source_sku == sku)
    )
    if brand:
        q = q.where(Product.brand == brand)
    res = await session.execute(q)
    return res.scalars().first()


async def get_products_in_category(
    session: AsyncSession,
    category: str,
    exclude_product_id: Optional[int] = None,
) -> Sequence[Product]:
    """Вернуть товары категории с подгруженными характеристиками.

    `exclude_product_id` отсекает сам target прямо в SQL — иначе он
    оказался бы в кандидатах со скором 1.0 и портил выдачу.
    Результат ограничен `MAX_CANDIDATES` (env, default 500).
    """
    q = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(Product.category == category)
        .limit(_MAX_CANDIDATES + 1)
    )
    if exclude_product_id is not None:
        q = q.where(Product.id != exclude_product_id)
    res = await session.execute(q)
    products = list(res.scalars().all())
    if len(products) > _MAX_CANDIDATES:
        _log.warning(
            "Category %r exceeds candidate cap (%d). Set MAX_CANDIDATES env to raise it.",
            category,
            _MAX_CANDIDATES,
        )
        products = products[:_MAX_CANDIDATES]
    return products


async def upsert_product(session: AsyncSession, product_data: dict) -> Product:
    """Атомарно создать или обновить товар по `(source_id, source_sku)`.

    Использует PostgreSQL INSERT … ON CONFLICT DO UPDATE, что исключает
    дубли при параллельных запусках скраперов.
    """
    set_cols = {
        k: product_data[k]
        for k in product_data
        if k not in ("id", "source_id", "source_sku", "created_at")
    }
    stmt = (
        pg_insert(Product)
        .values(**product_data)
        .on_conflict_do_update(
            constraint="uq_products_source_sku",
            set_={**set_cols, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
    await session.commit()
    r = await session.execute(
        select(Product).where(
            Product.source_id == product_data["source_id"],
            Product.source_sku == product_data["source_sku"],
        )
    )
    return r.scalars().one()


async def add_spec(
    session: AsyncSession,
    product_id: int,
    spec_name: str,
    value_text: Optional[str] = None,
    value_num: Optional[float] = None,
    unit: Optional[str] = None,
    spec_name_canonical: Optional[str] = None,
    weight: float = 1.0,
) -> ProductSpec:
    """Добавить одну характеристику товара и сохранить в БД."""
    s = ProductSpec(
        product_id=product_id,
        spec_name=spec_name,
        spec_name_canonical=spec_name_canonical or spec_name,
        spec_value_text=value_text,
        spec_value_num=value_num,
        spec_unit=unit,
        weight=weight,
    )
    session.add(s)
    await session.commit()
    return s


async def get_brands(session: AsyncSession) -> list[str]:
    """Вернуть отсортированный список непустых брендов из таблицы товаров."""
    r = await session.execute(
        select(Product.brand).distinct().order_by(Product.brand)
    )
    return [b for b in r.scalars() if b]


async def get_last_updated(session: AsyncSession) -> Optional[datetime]:
    """Вернуть время последнего обновления любого товара."""
    r = await session.execute(select(func.max(Product.updated_at)))
    return r.scalar()


async def create_source_if_missing(
    session: AsyncSession,
    name: str,
    base_url: str,
) -> Source:
    """Найти источник по `base_url` или создать новый."""
    q = select(Source).where(Source.base_url == base_url)
    r = await session.execute(q)
    existing = r.scalars().first()
    if existing:
        return existing
    src = Source(name=name, base_url=base_url)
    session.add(src)
    await session.commit()
    await session.refresh(src)
    return src

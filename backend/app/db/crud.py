"""CRUD-операции над товарами, характеристиками и источниками."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Product, ProductSpec, Source


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
    """Вернуть все товары категории с подгруженными характеристиками.

    `exclude_product_id` отсекает сам target прямо в SQL — иначе он
    оказался бы в кандидатах со скором 1.0 и портил выдачу.
    """
    q = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(Product.category == category)
    )
    if exclude_product_id is not None:
        q = q.where(Product.id != exclude_product_id)
    res = await session.execute(q)
    return res.scalars().all()


async def upsert_product(session: AsyncSession, product_data: dict) -> Product:
    """Создать товар или обновить существующий по `(source_id, source_sku)`."""
    q = select(Product).where(
        Product.source_id == product_data["source_id"],
        Product.source_sku == product_data["source_sku"],
    )
    r = await session.execute(q)
    p = r.scalars().first()
    if p:
        for k, v in product_data.items():
            if hasattr(p, k):
                setattr(p, k, v)
        await session.commit()
        return p
    else:
        p = Product(**product_data)
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


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

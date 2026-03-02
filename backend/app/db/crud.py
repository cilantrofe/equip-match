from sqlalchemy import select
from app.db.models import Product, ProductSpec, Source
from sqlalchemy.ext.asyncio import AsyncSession

async def get_product_by_sku(session: AsyncSession, sku: str):
    q = select(Product).where(Product.source_sku == sku)
    res = await session.execute(q)
    return res.scalars().first()

async def get_products_in_category(session: AsyncSession, category: str):
    q = select(Product).where(Product.category == category)
    res = await session.execute(q)
    return res.scalars().all()

async def upsert_product(session: AsyncSession, product_data: dict):
    q = select(Product).where(Product.source_id == product_data["source_id"], Product.source_sku == product_data["source_sku"])
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

async def add_spec(session: AsyncSession, product_id: int, spec_name: str, value_text: str = None, value_num=None, unit: str = None):
    s = ProductSpec(product_id=product_id, spec_name=spec_name, spec_value_text=value_text, spec_value_num=value_num, spec_unit=unit)
    session.add(s)
    await session.commit()
    return s

async def create_source_if_missing(session: AsyncSession, name: str, base_url: str):
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

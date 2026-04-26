"""ORM-модели базы данных."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Numeric, Text, TIMESTAMP
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Source(Base):
    """Источник товаров (сайт-скрапер)."""

    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    base_url = Column(Text)
    last_scraped = Column(TIMESTAMP)


class Product(Base):
    """Товар из конкретного источника."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"))
    source_sku = Column(Text, index=True)
    brand = Column(Text)
    model = Column(Text)
    category = Column(Text, index=True)
    price = Column(Numeric)
    currency = Column(Text)
    url = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    specs = relationship("ProductSpec", back_populates="product")


class ProductSpec(Base):
    """Одна характеристика товара."""

    __tablename__ = "product_specs"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    spec_name = Column(Text)
    spec_name_canonical = Column(Text, index=True)
    spec_value_text = Column(Text)
    spec_value_num = Column(Numeric)
    spec_unit = Column(Text)
    weight = Column(Numeric, nullable=False, server_default="1.0")

    product = relationship("Product", back_populates="specs")

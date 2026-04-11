from sqlalchemy import Column, Integer, Text, Numeric, ForeignKey, TIMESTAMP
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    base_url = Column(Text)
    last_scraped = Column(TIMESTAMP)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"))
    source_sku = Column(Text)
    brand = Column(Text)
    model = Column(Text)
    category = Column(Text)
    price = Column(Numeric)
    currency = Column(Text)
    url = Column(Text)
    raw_html = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    specs = relationship("ProductSpec", back_populates="product")


class ProductSpec(Base):
    __tablename__ = "product_specs"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    spec_name = Column(Text)
    spec_value_text = Column(Text)
    spec_value_num = Column(Numeric)
    spec_unit = Column(Text)

    product = relationship("Product", back_populates="specs")

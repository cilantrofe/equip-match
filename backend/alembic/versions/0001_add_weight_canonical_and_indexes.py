"""add weight, spec_name_canonical and indexes

Добавляет в `product_specs` колонки `weight` (вес для формулы матчинга)
и `spec_name_canonical` (каноническое имя характеристики для сравнения
без алиасов). Создаёт индексы на полях, по которым матчер фильтрует и
джойнит чаще всего.

Revision ID: 0001
Revises:
Create Date: 2026-04-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_specs",
        sa.Column(
            "weight",
            sa.Numeric(),
            server_default="1.0",
            nullable=False,
        ),
    )
    op.add_column(
        "product_specs",
        sa.Column("spec_name_canonical", sa.Text(), nullable=True),
    )

    op.create_index(
        "idx_product_specs_canonical",
        "product_specs",
        ["spec_name_canonical"],
    )
    op.create_index(
        "idx_product_specs_product_id",
        "product_specs",
        ["product_id"],
    )
    op.create_index(
        "idx_products_category",
        "products",
        ["category"],
    )
    op.create_index(
        "idx_products_source_sku",
        "products",
        ["source_sku"],
    )


def downgrade() -> None:
    op.drop_index("idx_products_source_sku", table_name="products")
    op.drop_index("idx_products_category", table_name="products")
    op.drop_index("idx_product_specs_product_id", table_name="product_specs")
    op.drop_index("idx_product_specs_canonical", table_name="product_specs")
    op.drop_column("product_specs", "spec_name_canonical")
    op.drop_column("product_specs", "weight")

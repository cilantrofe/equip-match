"""unique (source_id, source_sku) and nullable weight

Два изменения:

1. Добавляет UNIQUE CONSTRAINT на (source_id, source_sku) в таблице products,
   чтобы исключить дубли при параллельных запусках скраперов.
   Перед созданием ограничения удаляет дублирующие строки, оставляя
   запись с максимальным id (и её характеристики).

2. Делает колонку product_specs.weight nullable. Для новых характеристик, у которых еще нет весов.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM product_specs
        WHERE product_id IN (
            SELECT id FROM products
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM products
                GROUP BY source_id, source_sku
            )
        )
    """)
    op.execute("""
        DELETE FROM products
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM products
            GROUP BY source_id, source_sku
        )
    """)
    op.create_unique_constraint(
        "uq_products_source_sku",
        "products",
        ["source_id", "source_sku"],
    )

    op.alter_column(
        "product_specs",
        "weight",
        existing_type=sa.Numeric(),
        nullable=True,
        server_default=None,
        existing_server_default="1.0",
        existing_nullable=False,
    )
    op.execute("UPDATE product_specs SET weight = NULL WHERE weight = 1.0")


def downgrade() -> None:
    op.execute("UPDATE product_specs SET weight = 1.0 WHERE weight IS NULL")
    op.alter_column(
        "product_specs",
        "weight",
        existing_type=sa.Numeric(),
        nullable=False,
        server_default="1.0",
        existing_server_default=None,
        existing_nullable=True,
    )
    op.drop_constraint("uq_products_source_sku", "products", type_="unique")

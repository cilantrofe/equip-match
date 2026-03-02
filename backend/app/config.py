import os
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://pm:pm_pass@localhost:5432/product_matcher")

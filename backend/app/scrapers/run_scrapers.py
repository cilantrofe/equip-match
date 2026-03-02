import asyncio
from app.scrapers.basip_scraper import crawl_catalog_and_products

if __name__ == "__main__":
    asyncio.run(crawl_catalog_and_products())
